"""Authenticated, server-rendered Community HMS Quick Demo routes."""
from __future__ import annotations
import secrets
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.auth.session import get_csrf_token, validate_csrf
from app.config import get_settings
from app.db import get_db
from app.dependencies import get_current_user
from app.html_render import render_template
from app.i18n import resolve_locale, with_locale
from app.models import User
from app.services.quick_demo_service import QuickDemoError, create_session, enabled, owned_session, public_url, status_context
from app.view_context import user_to_dict

router=APIRouter(tags=["quick-demo"])
_OWNER_KEY="quick_demo_browser_secret"; _NONCE_KEY="quick_demo_request_nonce"

def _gate():
    if not enabled(get_settings()): raise HTTPException(404,"Not found")
def _owner(request,create=False):
    value=request.session.get(_OWNER_KEY)
    if not value and create:
        value=secrets.token_urlsafe(32);request.session[_OWNER_KEY]=value
    return value
def _http(exc): return HTTPException(exc.status,exc.code)
def _json_preferred(request:Request)->bool:
    values={}
    for part in (request.headers.get("accept") or "text/html").split(","):
        bits=[x.strip() for x in part.split(";")]; q=1.0
        for bit in bits[1:]:
            if bit.startswith("q="):
                try:q=float(bit[2:])
                except ValueError:q=0
        values[bits[0].lower()]=q
    return values.get("application/json",0)>max(values.get("text/html",0),values.get("application/xhtml+xml",0))
def _private_headers(): return {"Cache-Control":"no-store","Vary":"Accept"}

@router.get("/quick-demo/hms",response_class=HTMLResponse)
def hms_page(request:Request,user:User=Depends(get_current_user))->HTMLResponse:
    _gate();_owner(request,True); language=resolve_locale(request)
    return _render_start(request,user,language)

def _render_start(request,user,language):
    response=render_template(request,"quick_demo/start.html",{
        "user":user_to_dict(user),"solution_code":"hms","edition":"community","language":language,
        "sessions_action":"/quick-demo/sessions","back_href":"/catalog?solution=hms",
    })
    response.headers.update(_private_headers());return response

@router.post("/quick-demo/sessions")
def start_session(request:Request,solution_code:str=Form(...),edition:str=Form(...),language:str=Form(...),
                  csrf_token:str=Form(""),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    _gate()
    if not validate_csrf(request,csrf_token): raise HTTPException(403,"Invalid CSRF token")
    secret=_owner(request)
    if not secret: raise HTTPException(403,"Browser session required")
    nonce=request.session.get(_NONCE_KEY)
    if not nonce: nonce=secrets.token_urlsafe(24);request.session[_NONCE_KEY]=nonce
    try:item=create_session(db,get_settings(),browser_secret=secret,request_nonce=nonce,owner_user_id=user.id,
                            solution_code=solution_code,edition=edition,language=language)
    except QuickDemoError as exc: raise _http(exc) from exc
    response=RedirectResponse(with_locale(f"/quick-demo/status/{item.public_id}",language),303)
    response.headers.update(_private_headers());return response

@router.get("/quick-demo/status/{public_id}")
def session_status(public_id:str,request:Request,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    _gate()
    try:item=owned_session(db,public_id,_owner(request),user.id)
    except QuickDemoError as exc: raise _http(exc) from exc
    ctx=status_context(db,item,get_settings())
    if ctx["retry_allowed"]:request.session.pop(_NONCE_KEY,None)
    if _json_preferred(request): return JSONResponse(ctx,headers=_private_headers())
    ctx.update({
        "user":user_to_dict(user),"status_poll_url":f"/quick-demo/status/{item.public_id}",
        "retry_href":with_locale("/quick-demo/hms",item.language),"back_href":"/catalog?solution=hms",
    })
    response=render_template(request,"quick_demo/status.html",ctx);response.headers.update(_private_headers());return response

@router.get("/quick-demo/open/{public_id}")
def open_session(public_id:str,request:Request,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    _gate()
    try:item=owned_session(db,public_id,_owner(request),user.id)
    except QuickDemoError as exc: raise _http(exc) from exc
    ctx=status_context(db,item,get_settings());url=public_url(item,get_settings()) if ctx["can_launch"] else None
    if not url: raise HTTPException(409,"Session is not launchable")
    response=RedirectResponse(url,303);response.headers.update(_private_headers());return response
