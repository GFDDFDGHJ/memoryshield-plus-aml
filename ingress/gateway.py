"""Credential-routed transport for identical Textual/Coding public URLs.

No memory imports, payload inspection, database access, or model calls.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
import httpx


@dataclass(frozen=True)
class GatewaySettings:
    text_token: str
    coding_token: str
    text_url: str = 'http://127.0.0.1:8080'
    coding_url: str = 'http://127.0.0.1:8181'

    def __post_init__(self):
        if not self.text_token or not self.coding_token or self.text_token==self.coding_token:
            raise ValueError('Distinct nonempty track credentials are required')
        if self.text_url!='http://127.0.0.1:8080' or self.coding_url!='http://127.0.0.1:8181':
            raise ValueError('Only fixed loopback backends are permitted')


def load_settings():
    root=Path(__file__).resolve().parents[1]
    return GatewaySettings(
        text_token=(root/'.secrets/aml_auth_token.txt').read_text(encoding='utf-8').strip(),
        coding_token=(root/'coding/.secrets/auth_token.txt').read_text(encoding='utf-8').strip())


def create_app(settings=None, *, transport=None):
    # Tests inject fake credentials; only a live app loads deployment secrets.
    configuration=settings

    @asynccontextmanager
    async def lifespan(api):
        api.state.settings=configuration if configuration is not None else load_settings()
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,
                limits=httpx.Limits(max_connections=80,max_keepalive_connections=32),
                timeout=httpx.Timeout(300,connect=3,write=60,pool=10)) as client:
            api.state.upstream=client
            yield

    api=FastAPI(title='MemoryShield+ track gateway',version='v1.0.0',lifespan=lifespan,
                docs_url=None,redoc_url=None,openapi_url=None)

    def route(request, *, public_health=False):
        config=api.state.settings
        auth_values=request.headers.getlist('authorization')
        key_values=request.headers.getlist('x-api-key')
        if len(auth_values)>1 or len(key_values)>1:
            raise HTTPException(401,detail={'reason':'invalid_auth_token'})
        auth=(auth_values[0] if auth_values else '').strip()
        key=(key_values[0] if key_values else '').strip()
        if auth:
            parts=auth.split(None,1)
            if len(parts)==2 and parts[0].lower() in {'bearer','token'}:
                auth=parts[1].strip()
        if auth and key and not hmac.compare_digest(auth.encode(),key.encode()):
            raise HTTPException(401,detail={'reason':'invalid_auth_token'})
        presented=auth or key
        if not presented and public_health:
            return 'textual',config.text_url,config.text_token
        if presented and hmac.compare_digest(presented.encode(),config.text_token.encode()):
            return 'textual',config.text_url,config.text_token
        if presented and hmac.compare_digest(presented.encode(),config.coding_token.encode()):
            return 'coding',config.coding_url,config.coding_token
        raise HTTPException(401,detail={'reason':'invalid_auth_token'})

    async def proxy(request,path,*,public_health=False):
        track,base,token=route(request,public_health=public_health)
        # Caller-controlled URLs, track headers, query strings and forwarding
        # headers are never used to select or address an upstream.
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
        if request.headers.get('content-type'):
            headers['Content-Type']=request.headers['content-type']
        try:
            options={'timeout':5} if public_health else {}
            response=await api.state.upstream.request(request.method,base+path,
                content=await request.body(),headers=headers,**options)
        except httpx.TimeoutException:
            return JSONResponse({'detail':{'reason':'memory_upstream_timeout'}},status_code=504)
        except httpx.HTTPError:
            return JSONResponse({'detail':{'reason':'memory_upstream_unavailable'}},status_code=503)
        # Preserve the decoded response entity and status. Transport headers
        # are rebuilt so decompression cannot leave a stale Content-Length.
        outgoing={'X-Memory-Track':track}
        for name in ('content-type','retry-after'):
            if name in response.headers:outgoing[name]=response.headers[name]
        return Response(content=response.content,status_code=response.status_code,headers=outgoing)

    @api.get('/__gateway_health')
    async def gateway_health():
        return {'status':'ok','system':'MemoryShield+ track gateway','version':'v1.0.0'}

    @api.get('/health')
    async def health(request:Request):
        return await proxy(request,'/health',public_health=True)

    @api.post('/add')
    async def add(request:Request):
        return await proxy(request,'/add')

    @api.post('/search')
    async def search(request:Request):
        return await proxy(request,'/search')

    return api


app=create_app()
