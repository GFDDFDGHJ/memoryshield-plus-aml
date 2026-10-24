"""Credential transport with an explicit local mode for the original key.

No memory imports, payload inspection, database access, or model calls.
"""
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
import hmac
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
import httpx

try:
    from .legacy_mode import LegacyModeController, LegacyModeBusy, LegacyModeUnavailable
except ImportError:
    from legacy_mode import LegacyModeController, LegacyModeBusy, LegacyModeUnavailable

ROOT=Path(__file__).resolve().parent


def load_legacy_target(path=None):
    """Read only an operator-owned local mode file; public requests cannot set it."""
    target=ROOT/'legacy_target.json' if path is None else Path(path)
    raw=target.read_bytes()
    if len(raw)>4096:raise ValueError('Invalid local legacy mode')
    value=json.loads(raw.decode('utf-8'))
    if not isinstance(value,dict) or not isinstance(value.get('legacy_key_target'),str) or value['legacy_key_target'] not in {'textual','coding'}:
        raise ValueError('Invalid local legacy mode')
    return value['legacy_key_target']


@dataclass(frozen=True)
class GatewaySettings:
    text_token: str
    coding_token: str
    text_url: str = 'http://127.0.0.1:8080'
    coding_url: str = 'http://127.0.0.1:8181'
    legacy_key_target: str = 'textual'

    def __post_init__(self):
        if not self.text_token or not self.coding_token or self.text_token==self.coding_token:
            raise ValueError('Distinct nonempty track credentials are required')
        if self.text_url!='http://127.0.0.1:8080' or self.coding_url!='http://127.0.0.1:8181':
            raise ValueError('Only fixed loopback backends are permitted')
        if self.legacy_key_target not in {'textual','coding'}:
            raise ValueError('Invalid local legacy mode')


def load_settings():
    root=Path(__file__).resolve().parents[1]
    return GatewaySettings(
        text_token=(root/'.secrets/aml_auth_token.txt').read_text(encoding='utf-8').strip(),
        coding_token=(root/'coding/.secrets/auth_token.txt').read_text(encoding='utf-8').strip())


def create_app(settings=None, *, transport=None, mode_controller=None):
    # Tests inject fake credentials; only a live app loads deployment secrets.
    configuration=settings

    @asynccontextmanager
    async def lifespan(api):
        api.state.settings=configuration if configuration is not None else load_settings()
        reader=(lambda:configuration.legacy_key_target) if configuration is not None else load_legacy_target
        api.state.legacy_mode=mode_controller if mode_controller is not None else LegacyModeController(reader)
        api.state.active_memory_requests=0
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,
                limits=httpx.Limits(max_connections=80,max_keepalive_connections=32),
                timeout=httpx.Timeout(300,connect=3,write=60,pool=10)) as client:
            api.state.upstream=client
            yield

    api=FastAPI(title='MemoryShield+ track gateway',version='v1.1.0',lifespan=lifespan,
                docs_url=None,redoc_url=None,openapi_url=None)

    def principal(request, *, public_health=False):
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
            return 'anonymous'
        if presented and hmac.compare_digest(presented.encode(),config.text_token.encode()):
            return 'legacy'
        if presented and hmac.compare_digest(presented.encode(),config.coding_token.encode()):
            return 'coding'
        raise HTTPException(401,detail={'reason':'invalid_auth_token'})

    async def proxy(request,path,*,public_health=False):
        identity=principal(request,public_health=public_health)
        # Caller-controlled URLs, track headers, query strings and forwarding
        # headers are never used to select or address an upstream.
        context=api.state.legacy_mode.request() if identity=='legacy' and not public_health else nullcontext()
        try:
            with context as selected:
                track='coding' if identity=='coding' else 'textual'
                if identity=='legacy':
                    if public_health:
                        state=api.state.legacy_mode.status()
                        if not state['legacy_config_valid']:raise LegacyModeUnavailable()
                        track=state['legacy_key_target']
                    else:track=selected
                config=api.state.settings
                base,token=(config.coding_url,config.coding_token) if track=='coding' else (config.text_url,config.text_token)
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
                if request.headers.get('content-type'):
                    headers['Content-Type']=request.headers['content-type']
                if not public_health:api.state.active_memory_requests+=1
                try:
                    options={'timeout':5} if public_health else {}
                    response=await api.state.upstream.request(request.method,base+path,
                        content=await request.body(),headers=headers,**options)
                    # Retain the decoded entity/status and rebuild transport headers.
                    outgoing={'X-Memory-Track':track}
                    for name in ('content-type','retry-after'):
                        if name in response.headers:outgoing[name]=response.headers[name]
                    return Response(content=response.content,status_code=response.status_code,headers=outgoing)
                finally:
                    if not public_health:api.state.active_memory_requests-=1
        except LegacyModeBusy:
            return JSONResponse({'detail':{'reason':'legacy_track_switch_pending'}},status_code=503,headers={'Retry-After':'1'})
        except LegacyModeUnavailable:
            return JSONResponse({'detail':{'reason':'legacy_track_configuration_unavailable'}},status_code=503)
        except httpx.TimeoutException:
            return JSONResponse({'detail':{'reason':'memory_upstream_timeout'}},status_code=504)
        except httpx.HTTPError:
            return JSONResponse({'detail':{'reason':'memory_upstream_unavailable'}},status_code=503)

    @api.get('/__gateway_health')
    async def gateway_health():
        return {'status':'ok','system':'MemoryShield+ track gateway','version':'v1.1.0',
                'active_memory_requests':api.state.active_memory_requests,**api.state.legacy_mode.status()}

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
