import os
import json
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Depends, Request, Response, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import init_db, SessionLocal, SystemConfig, User, Notice, SMTPSettings, Subscriber, OIDCSettings, UptimeKumaSettings
import auth
import smtp
import kuma

app = FastAPI(title="OmniBanner Central Service")

# CORS config to allow WordPress plugins to fetch banners
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# DB Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper to verify dashboard authentication via Cookie
async def get_current_user_cookie(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        # Check authorization header as fallback
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            
    if not token:
        return None
        
    payload = auth.decode_access_token(token)
    if not payload:
        return None
        
    username = payload.get("sub")
    if not username:
        return None
        
    user = db.query(User).filter(User.username == username).first()
    return user

# Helper to get brand info dynamically
def get_brand_context(db: Session):
    config = db.query(SystemConfig).first()
    if not config:
        config = SystemConfig(app_name="OmniBanner", app_icon_url="/static/brand/logo.png", primary_color="#8b5cf6")
    return {
        "app_name": config.app_name,
        "app_icon_url": config.app_icon_url,
        "primary_color": config.primary_color
    }

# Check setup state middleware / helper
def is_setup_completed(db: Session) -> bool:
    config = db.query(SystemConfig).first()
    return config and config.setup_completed

# --- PAGE ROUTERS ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
    return RedirectResponse(url="/dashboard")

@app.get("/setup", response_class=HTMLResponse)
async def get_setup(request: Request, db: Session = Depends(get_db)):
    if is_setup_completed(db):
        return RedirectResponse(url="/login")
        
    ctx = {
        "request": request,
        "app_name": "OmniBanner Initial Setup",
        "app_icon_url": "/static/brand/logo.png",
        "primary_color": "#8b5cf6",
        "show_nav": False
    }
    return templates.TemplateResponse("setup.html", ctx)

@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
        
    # Check current token
    user = await get_current_user_cookie(request, db)
    if user:
        return RedirectResponse(url="/dashboard")
        
    brand = get_brand_context(db)
    
    # Determine auth style
    oidc = db.query(OIDCSettings).first()
    auth_mode = "credentials"
    oidc_provider = "SSO"
    if oidc and oidc.enabled:
        auth_mode = "oidc"
        oidc_provider = oidc.provider_name
        
    ctx = {
        "request": request,
        "app_name": brand["app_name"],
        "app_icon_url": brand["app_icon_url"],
        "primary_color": brand["primary_color"],
        "show_nav": False,
        "auth_mode": auth_mode,
        "oidc_provider": oidc_provider
    }
    return templates.TemplateResponse("login.html", ctx)

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
        
    user = await get_current_user_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
        
    brand = get_brand_context(db)
    ctx = {
        "request": request,
        "app_name": brand["app_name"],
        "app_icon_url": brand["app_icon_url"],
        "primary_color": brand["primary_color"],
        "show_nav": True,
        "user_display": user.username
    }
    return templates.TemplateResponse("dashboard.html", ctx)

@app.get("/totp-setup", response_class=HTMLResponse)
async def get_totp_setup(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
        
    user = await get_current_user_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
        
    brand = get_brand_context(db)
    
    # Generate secrets if not created already
    secret = auth.generate_totp_secret()
    totp_uri = auth.get_totp_uri(secret, user.username, issuer_name=brand["app_name"])
    
    ctx = {
        "request": request,
        "app_name": brand["app_name"],
        "app_icon_url": brand["app_icon_url"],
        "primary_color": brand["primary_color"],
        "show_nav": True,
        "totp_secret": secret,
        "totp_uri": totp_uri
    }
    return templates.TemplateResponse("totp_setup.html", ctx)

@app.get("/logout")
async def get_logout(response: Response):
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("access_token")
    return resp

# --- SETUP API ENDPOINT ---

@app.post("/api/setup")
async def post_setup(data: dict, db: Session = Depends(get_db)):
    # Check if setup is already complete
    config = db.query(SystemConfig).first()
    if config and config.setup_completed:
        raise HTTPException(status_code=400, detail="Setup already completed")
        
    # 1. Update branding settings
    if not config:
        config = SystemConfig()
        db.add(config)
    config.app_name = data.get("app_name", "OmniBanner")
    config.app_icon_url = data.get("app_icon_url", "/static/brand/logo.png")
    config.primary_color = data.get("primary_color", "#8b5cf6")
    config.setup_completed = True
    
    # 2. Setup auth mode
    auth_mode = data.get("auth_mode", "credentials")
    if auth_mode == "credentials":
        username = data.get("admin_username", "admin")
        password = data.get("admin_password")
        if not password:
            raise HTTPException(status_code=400, detail="Password is required for credentials setup")
        
        # Clear existing users
        db.query(User).delete()
        
        admin = User(
            username=username,
            hashed_password=auth.get_password_hash(password),
            totp_enabled=data.get("enable_totp", False)
        )
        if data.get("enable_totp"):
            admin.totp_secret = auth.generate_totp_secret()
            
        db.add(admin)
    else:
        # Create dummy admin placeholder for OIDC mapped users
        db.query(User).delete()
        # Setup OIDC Settings
        oidc = db.query(OIDCSettings).first()
        if not oidc:
            oidc = OIDCSettings()
            db.add(oidc)
        oidc.provider_name = data.get("oidc_provider_name", "Authentik")
        oidc.issuer_url = data.get("oidc_issuer_url", "")
        oidc.client_id = data.get("oidc_client_id", "")
        oidc.client_secret = data.get("oidc_client_secret", "")
        oidc.enabled = True
        
    # 3. Setup SMTP Settings (Optional)
    smtp_host = data.get("smtp_host")
    if smtp_host:
        smtp_conf = db.query(SMTPSettings).first()
        if not smtp_conf:
            smtp_conf = SMTPSettings()
            db.add(smtp_conf)
        smtp_conf.host = smtp_host
        smtp_conf.port = int(data.get("smtp_port", 587))
        smtp_conf.username = data.get("smtp_username", "")
        smtp_conf.password = data.get("smtp_password", "")
        smtp_conf.from_email = data.get("smtp_from_email", "")
        smtp_conf.from_name = data.get("smtp_from_name", "OmniBanner Alerts")
        smtp_conf.enabled = True
        
    # 4. Setup Uptime Kuma Settings (Optional)
    kuma_url = data.get("kuma_url")
    if kuma_url:
        kuma_conf = db.query(UptimeKumaSettings).first()
        if not kuma_conf:
            kuma_conf = UptimeKumaSettings()
            db.add(kuma_conf)
        kuma_conf.url = kuma_url
        kuma_conf.username = data.get("kuma_username", "")
        kuma_conf.password = data.get("kuma_password", "")
        kuma_conf.enabled = True
        
    db.commit()
    return {"success": True}

# --- AUTH API ENDPOINTS ---

@app.post("/api/login-step1")
async def login_step1(credentials: dict, db: Session = Depends(get_db)):
    username = credentials.get("username")
    password = credentials.get("password")
    
    user = db.query(User).filter(User.username == username).first()
    if not user or not auth.verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
        
    # Check if MFA is required
    if user.totp_enabled:
        return {"mfa_required": True}
        
    # Standard JWT generation
    token = auth.create_access_token(data={"sub": user.username})
    return {"mfa_required": False, "access_token": token}

@app.post("/api/login-step2")
async def login_step2(credentials: dict, db: Session = Depends(get_db)):
    username = credentials.get("username")
    password = credentials.get("password")
    mfa_code = credentials.get("mfa_code")
    
    user = db.query(User).filter(User.username == username).first()
    if not user or not auth.verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled on this account")
        
    if not user.totp_secret:
        raise HTTPException(status_code=500, detail="MFA secret key is missing")
        
    if not auth.verify_totp_code(user.totp_secret, mfa_code):
        raise HTTPException(status_code=401, detail="Invalid verification code")
        
    token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": token}

# --- OIDC REDIRECT & CALLBACK ---

@app.get("/auth/oidc/login")
async def oidc_login(db: Session = Depends(get_db)):
    oidc = db.query(OIDCSettings).first()
    if not oidc or not oidc.enabled:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")
        
    discovery = await auth.discover_oidc_endpoints(oidc.issuer_url)
    if not discovery:
        raise HTTPException(status_code=500, detail="OIDC endpoint discovery failed")
        
    auth_endpoint = discovery.get("authorization_endpoint")
    
    # We build callback URL dynamically using request host
    # In sandbox or docker, standard localhost redirect is usually configured
    # We will assume client passes redirect URI or it is /auth/oidc/callback
    # Build query
    client_id = oidc.client_id
    redirect_uri = "http://localhost:8000/auth/oidc/callback" # Fallback local developer redirect
    
    # Redirect user
    scope = "openid profile email"
    redirect_url = f"{auth_endpoint}?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope={scope}&state=omnibanner_state"
    return RedirectResponse(url=redirect_url)

@app.get("/auth/oidc/callback")
async def oidc_callback(code: str, request: Request, db: Session = Depends(get_db)):
    oidc = db.query(OIDCSettings).first()
    if not oidc or not oidc.enabled:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")
        
    discovery = await auth.discover_oidc_endpoints(oidc.issuer_url)
    if not discovery:
        raise HTTPException(status_code=500, detail="OIDC discovery failed")
        
    token_endpoint = discovery.get("token_endpoint")
    userinfo_endpoint = discovery.get("userinfo_endpoint")
    
    redirect_uri = "http://localhost:8000/auth/oidc/callback"
    
    # Exchange code
    tokens = await auth.exchange_oidc_code(
        token_endpoint=token_endpoint,
        client_id=oidc.client_id,
        client_secret=oidc.client_secret,
        code=code,
        redirect_uri=redirect_uri
    )
    if not tokens:
        raise HTTPException(status_code=400, detail="Failed to exchange authorization code")
        
    access_token = tokens.get("access_token")
    
    # Fetch userinfo
    userinfo = await auth.fetch_oidc_userinfo(userinfo_endpoint, access_token)
    if not userinfo:
        raise HTTPException(status_code=400, detail="Failed to fetch user profile from provider")
        
    # Get user identifier (sub)
    sub = userinfo.get("sub")
    email = userinfo.get("email")
    username = userinfo.get("preferred_username") or userinfo.get("username") or email or sub
    
    # Check or create user profile
    user = db.query(User).filter(User.oidc_sub == sub).first()
    if not user:
        # Create new profile mapped to OIDC provider
        user = User(username=username, hashed_password="", oidc_sub=sub, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
        
    # Create local application JWT token
    token = auth.create_access_token(data={"sub": user.username})
    resp = RedirectResponse(url="/dashboard")
    resp.set_cookie(key="access_token", value=token, max_age=86400, path="/")
    return resp

# --- MFA SETUP API ---

@app.post("/api/totp/verify")
async def verify_totp(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    secret = data.get("secret")
    code = data.get("code")
    
    if not auth.verify_totp_code(secret, code):
        raise HTTPException(status_code=400, detail="Invalid verification code")
        
    # Enable TOTP on profile
    user.totp_secret = secret
    user.totp_enabled = True
    db.commit()
    return {"success": True}

@app.post("/api/totp/disable")
async def disable_totp(user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    user.totp_enabled = False
    user.totp_secret = None
    db.commit()
    return {"success": True}

# --- NOTICE BANNERS CRUD ---

@app.get("/api/notices")
async def list_notices(user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return db.query(Notice).order_by(Notice.scheduled_start.desc()).all()

@app.post("/api/notices")
async def create_notice(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    # Convert dates from ISO strings to datetime objects
    start_dt = datetime.fromisoformat(data.get("scheduled_start"))
    end_dt = datetime.fromisoformat(data.get("scheduled_end"))
    
    notice = Notice(
        text=data.get("text"),
        bg_color=data.get("bg_color", "#ef4444"),
        text_color=data.get("text_color", "#ffffff"),
        display_duration=int(data.get("display_duration", 0)),
        dismissible=data.get("dismissible", True),
        dismiss_cooldown=int(data.get("dismiss_cooldown", 24)),
        scheduled_start=start_dt,
        scheduled_end=end_dt,
        target_type=data.get("target_type", "all"),
        target_sites=data.get("target_sites", ""),
        send_smtp=data.get("send_smtp", False),
        uptime_kuma_integration=data.get("uptime_kuma_integration", False),
        uptime_kuma_push_url=data.get("uptime_kuma_push_url"),
        uptime_kuma_monitor_ids=data.get("uptime_kuma_monitor_ids")
    )
    
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return notice

@app.put("/api/notices/{notice_id}")
async def update_notice(notice_id: int, data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
        
    notice.text = data.get("text")
    notice.bg_color = data.get("bg_color")
    notice.text_color = data.get("text_color")
    notice.display_duration = int(data.get("display_duration", 0))
    notice.dismissible = data.get("dismissible", True)
    notice.dismiss_cooldown = int(data.get("dismiss_cooldown", 24))
    notice.scheduled_start = datetime.fromisoformat(data.get("scheduled_start"))
    notice.scheduled_end = datetime.fromisoformat(data.get("scheduled_end"))
    notice.target_type = data.get("target_type")
    notice.target_sites = data.get("target_sites")
    notice.send_smtp = data.get("send_smtp", False)
    notice.uptime_kuma_integration = data.get("uptime_kuma_integration", False)
    notice.uptime_kuma_push_url = data.get("uptime_kuma_push_url")
    notice.uptime_kuma_monitor_ids = data.get("uptime_kuma_monitor_ids")
    
    # If timeframe shifted back, reset smtp_sent to allow resending
    # or keep as is. Let's reset so they receive updates.
    notice.smtp_sent = False
    
    db.commit()
    return notice

@app.delete("/api/notices/{notice_id}")
async def delete_notice(notice_id: int, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
        
    db.delete(notice)
    db.commit()
    return {"success": True}

# --- SMTP API ---

@app.get("/api/smtp")
async def get_smtp(user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    smtp_conf = db.query(SMTPSettings).first()
    if not smtp_conf:
        return {"host": "", "port": 587, "username": "", "password": "", "from_email": "", "from_name": "", "enabled": False}
    return smtp_conf

@app.post("/api/smtp")
async def save_smtp(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    smtp_conf = db.query(SMTPSettings).first()
    if not smtp_conf:
        smtp_conf = SMTPSettings()
        db.add(smtp_conf)
        
    smtp_conf.host = data.get("host", "")
    smtp_conf.port = int(data.get("port", 587))
    smtp_conf.username = data.get("username", "")
    smtp_conf.password = data.get("password", "")
    smtp_conf.from_email = data.get("from_email", "")
    smtp_conf.from_name = data.get("from_name", "OmniBanner Alerts")
    smtp_conf.enabled = data.get("enabled", False)
    
    db.commit()
    return smtp_conf

@app.post("/api/test-smtp")
async def test_smtp(data: dict):
    # Sends a quick test email
    test_email = data.get("from_email")
    if not test_email:
        raise HTTPException(status_code=400, detail="Sender address required for test")
        
    subject = "OmniBanner - Live SMTP connection verification alert! ⚡"
    body = smtp.build_nice_email_html(
        app_name="OmniBanner Setup Test",
        primary_color="#8b5cf6",
        notice_text="🎉 Congrats! SMTP connection is successfully configured. You will now receive alerts when banner notices are scheduled.",
        start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    success = smtp.send_alert_email(data, test_email, subject, body)
    return {"success": success}

# --- SUBSCRIBERS LIST API ---

@app.get("/api/subscribers")
async def list_subscribers(user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return db.query(Subscriber).filter(Subscriber.active == True).all()

@app.post("/api/subscribers")
async def add_subscriber(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    email = data.get("email")
    name = data.get("name")
    
    # Check if subscriber exists
    existing = db.query(Subscriber).filter(Subscriber.email == email).first()
    if existing:
        if existing.active:
            raise HTTPException(status_code=400, detail="Subscriber email already registered")
        else:
            existing.active = True
            existing.name = name
            db.commit()
            return existing
            
    sub = Subscriber(email=email, name=name, active=True)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub

@app.delete("/api/subscribers/{sub_id}")
async def delete_subscriber(sub_id: int, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    sub = db.query(Subscriber).filter(Subscriber.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
        
    db.delete(sub)
    db.commit()
    return {"success": True}

# --- KUMA API ---

@app.get("/api/kuma")
async def get_kuma(user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    kuma_conf = db.query(UptimeKumaSettings).first()
    if not kuma_conf:
        return {"url": "", "username": "", "password": "", "enabled": False}
    return kuma_conf

@app.post("/api/kuma")
async def save_kuma(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    kuma_conf = db.query(UptimeKumaSettings).first()
    if not kuma_conf:
        kuma_conf = UptimeKumaSettings()
        db.add(kuma_conf)
        
    kuma_conf.url = data.get("url", "")
    kuma_conf.username = data.get("username", "")
    kuma_conf.password = data.get("password", "")
    kuma_conf.enabled = data.get("enabled", False)
    
    db.commit()
    return kuma_conf

@app.post("/api/test-kuma")
async def test_kuma(data: dict):
    # Simply attempts connection using the credentials
    url = data.get("url")
    username = data.get("username")
    password = data.get("password")
    
    # In tests, connecting using python-socketio client will verify immediately
    # We execute this inside executor pool to prevent server locks
    loop = asyncio.get_event_loop()
    # Try direct connection logic
    from uptime_kuma_api import UptimeKumaAPI
    def run_connect():
        try:
            with UptimeKumaAPI(url) as api:
                api.login(username, password)
                return True
        except Exception as e:
            print(f"Test Kuma Connection Error: {e}")
            return False
            
    success = await loop.run_in_executor(None, run_connect)
    return {"success": success}

# --- BRANDING & SECURITY API ---

@app.get("/api/brand")
async def get_brand(db: Session = Depends(get_db)):
    # Publicly accessible for OIDC title renders, setup, and WordPress fetches
    return get_brand_context(db)

@app.post("/api/brand")
async def save_brand(data: dict, user: User = Depends(get_current_user_cookie), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    config = db.query(SystemConfig).first()
    if not config:
        config = SystemConfig()
        db.add(config)
        
    config.app_name = data.get("app_name")
    config.app_icon_url = data.get("app_icon_url")
    config.primary_color = data.get("primary_color")
    
    db.commit()
    return config

@app.get("/api/security/status")
async def security_status(user: User = Depends(get_current_user_cookie)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"totp_enabled": user.totp_enabled}

# --- PUBLIC APIS FOR WORDPRESS PLUGINS ---

@app.get("/api/public/info")
async def public_info(db: Session = Depends(get_db)):
    # Endpoint consumed by WordPress plugin settings panel
    brand = get_brand_context(db)
    return {
        "name": brand["app_name"],
        "icon_url": brand["app_icon_url"],
        "color": brand["primary_color"]
    }

@app.get("/api/public/banner")
async def get_public_banner(domain: Optional[str] = None, db: Session = Depends(get_db)):
    # WordPress site queries active banner
    now = datetime.utcnow()
    
    # Query notices that:
    # 1. are active timeframe
    # 2. scope targets either "all" OR targets contains specific domain
    notices = db.query(Notice).filter(
        Notice.scheduled_start <= now,
        Notice.scheduled_end >= now
    ).all()
    
    # Filter notices based on domains
    matched_notice = None
    for n in notices:
        if n.target_type == "all":
            matched_notice = n
            break
        elif n.target_type == "domains" and domain:
            # Check domain matching
            target_list = [t.strip().lower() for t in n.target_sites.split(",") if t.strip()]
            if domain.strip().lower() in target_list:
                matched_notice = n
                break
                
    if not matched_notice:
        return JSONResponse(content={}, status_code=200)
        
    return {
        "id": matched_notice.id,
        "text": matched_notice.text,
        "bg_color": matched_notice.bg_color,
        "text_color": matched_notice.text_color,
        "display_duration": matched_notice.display_duration,
        "dismissible": matched_notice.dismissible,
        "dismiss_cooldown": matched_notice.dismiss_cooldown
    }

# --- BACKGROUND SYSTEM SCHEDULER TASK ---

async def notification_scheduler_loop():
    while True:
        try:
            db = SessionLocal()
            now = datetime.utcnow()
            
            # 1. Scan notices currently active to trigger SMTP campaigns
            active_notices = db.query(Notice).filter(
                Notice.scheduled_start <= now,
                Notice.scheduled_end >= now
            ).all()
            
            smtp_conf = db.query(SMTPSettings).filter(SMTPSettings.enabled == True).first()
            
            for notice in active_notices:
                if notice.send_smtp and not notice.smtp_sent and smtp_conf:
                    # Trigger subscriber notification campaign
                    subscribers = db.query(Subscriber).filter(Subscriber.active == True).all()
                    
                    if subscribers:
                        # Render HTML email
                        brand = db.query(SystemConfig).first()
                        app_name = brand.app_name if brand else "OmniBanner"
                        color = brand.primary_color if brand else "#8b5cf6"
                        
                        email_body = smtp.build_nice_email_html(
                            app_name=app_name,
                            primary_color=color,
                            notice_text=notice.text,
                            start_time=notice.scheduled_start.strftime("%Y-%m-%d %H:%M:%S"),
                            end_time=notice.scheduled_end.strftime("%Y-%m-%d %H:%M:%S"),
                            target_sites=notice.target_sites if notice.target_type == "domains" else None
                        )
                        
                        subject = f"[{app_name}] Scheduled Maintenance Notice: {notice.text[:50]}"
                        
                        smtp_data = {
                            "host": smtp_conf.host,
                            "port": smtp_conf.port,
                            "username": smtp_conf.username,
                            "password": smtp_conf.password,
                            "from_email": smtp_conf.from_email,
                            "from_name": smtp_conf.from_name
                        }
                        
                        for sub in subscribers:
                            smtp.send_alert_email(smtp_data, sub.email, subject, email_body)
                            
                    # Update status to sent
                    notice.smtp_sent = True
                    db.commit()
            
            # 2. Uptime Kuma push webhooks sync
            # Scan notices that JUST started or ended in the last minute (or sync every cycle)
            for notice in active_notices:
                if notice.uptime_kuma_integration and notice.uptime_kuma_push_url:
                    # Ping push URL saying monitor is down/in maintenance
                    await kuma.ping_kuma_push_url(notice.uptime_kuma_push_url, is_active=True, msg=notice.text)
                    
            # Find expired notices in the last 2 minutes that had Kuma enabled
            expired_notices = db.query(Notice).filter(
                Notice.scheduled_end < now,
                Notice.uptime_kuma_integration == True
            ).all()
            for notice in expired_notices:
                if notice.uptime_kuma_push_url:
                    await kuma.ping_kuma_push_url(notice.uptime_kuma_push_url, is_active=False, msg="Maintenance window finished")
            
            db.close()
        except Exception as e:
            print(f"Scheduler Loop Error: {e}")
            
        await asyncio.sleep(60) # Loop check every 60 seconds

@app.on_event("startup")
async def startup_event():
    init_db()
    # Launch background scheduler task
    asyncio.create_task(notification_scheduler_loop())
