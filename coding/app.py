"""Independent MemoryShield+ Coding API; source evidence only, no execution."""
import hmac
from pathlib import Path
import time
import threading
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from coding_config import CONFIG
from coding_models import ModelProvider
from coding_store import MemoryStore, AddRequestConflict, InvalidAddInput

class Message(BaseModel):
    role: Literal['user','assistant']
    content: str = Field(min_length=1)
    timestamp: int | None = Field(default=None,strict=True)

    @field_validator('role','content')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():
            raise ValueError('Non-empty text required')
        return value

class AddRequest(BaseModel):
    request_id: str = Field(min_length=1,max_length=1024)
    user_id: str = Field(min_length=1,max_length=1024)
    session_id: str = Field(min_length=1,max_length=1024)
    messages: list[Message] = Field(min_length=1)

    @field_validator('request_id','user_id','session_id')
    @classmethod
    def nonempty_identifier(cls,value):
        if not value.strip():
            raise ValueError('Non-empty identifier required')
        return value

class SearchRequest(BaseModel):
    user_id: str = Field(min_length=1,max_length=1024)
    query: str = Field(min_length=1)
    top_k: int = Field(ge=1,strict=True)
    options: list[str] | None = None

    @field_validator('user_id','query')
    @classmethod
    def nonempty_text(cls,value):
        if not value.strip():
            raise ValueError('Non-empty text required')
        return value

def create_app(memory=None, settings=CONFIG):
    api = FastAPI(title='MemoryShield+ Coding',version=settings.version)
    if memory is None:
        provider = ModelProvider(settings)
        memory = MemoryStore(settings.db_path,dim=settings.dim,embed=provider.embed,rerank=provider.rerank)
    api.state.memory = memory
    search_slots = threading.BoundedSemaphore(2)

    def authorize(authorization,x_api_key):
        if not settings.auth_token:
            raise HTTPException(503,detail={'reason':'coding_auth_not_configured'})
        value = (authorization or '').strip()
        if value:
            parts = value.split(None,1)
            if len(parts)==2 and parts[0].lower() in {'bearer','token'}:
                value = parts[1].strip()
        else:
            value = (x_api_key or '').strip()
        if not hmac.compare_digest(value.encode(),settings.auth_token.encode()):
            raise HTTPException(401,detail={'reason':'invalid_auth_token'})

    @api.get('/health')
    def health():
        return {'status':'ok','system':'MemoryShield+ Coding','track':'coding','version':settings.version,
                'embedding_model':settings.embedding_model,'rerank_enabled':settings.rerank_enabled,
                'rerank_model':settings.rerank_model,'source_revision':settings.source_revision,'ts':time.time()}

    @api.post('/add')
    def add(body:AddRequest,authorization:str|None=Header(default=None),x_api_key:str|None=Header(default=None)):
        authorize(authorization,x_api_key)
        try:
            written=memory.add_messages(body.user_id,body.session_id,[m.model_dump() for m in body.messages],request_id=body.request_id)
        except AddRequestConflict:
            raise HTTPException(409,detail={'reason':'request_id_conflict'}) from None
        except InvalidAddInput:
            raise HTTPException(422,detail={'reason':'invalid_coding_messages'}) from None
        except Exception:
            # Provider bodies, source code and credentials never become API errors.
            raise HTTPException(503,detail={'reason':'coding_add_unavailable'}) from None
        return {'success':True,'request_id':body.request_id,'user_id':body.user_id,
                'session_id':body.session_id,'written':written}

    @api.post('/search')
    def search(body:SearchRequest,authorization:str|None=Header(default=None),x_api_key:str|None=Header(default=None)):
        authorize(authorization,x_api_key)
        try:
            with search_slots:
                rows=memory.search(body.user_id,body.query,min(100,body.top_k))
        except Exception:
            raise HTTPException(503,detail={'reason':'coding_search_unavailable'}) from None
        return {'data':rows}

    return api

app=create_app()
