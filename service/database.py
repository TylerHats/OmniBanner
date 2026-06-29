import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "omnibanner.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True, index=True)
    setup_completed = Column(Boolean, default=False)
    app_name = Column(String, default="OmniBanner")
    app_icon_url = Column(String, default="/static/brand/logo.png")
    primary_color = Column(String, default="#4f46e5")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)
    oidc_sub = Column(String, unique=True, nullable=True)
    email = Column(String, nullable=True)

class Notice(Base):
    __tablename__ = "notices"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text)
    text_color = Column(String, default="#ffffff")
    bg_color = Column(String, default="#ef4444")
    display_duration = Column(Integer, default=0) # 0 for persistent, >0 for display seconds
    dismissible = Column(Boolean, default=True)
    dismiss_cooldown = Column(Integer, default=24) # hours
    scheduled_start = Column(DateTime)
    scheduled_end = Column(DateTime)
    target_type = Column(String, default="all") # "all" or "domains"
    target_sites = Column(String, nullable=True) # comma-separated list of domains
    send_smtp = Column(Boolean, default=False)
    smtp_sent = Column(Boolean, default=False) # tracking if email campaign went out
    uptime_kuma_integration = Column(Boolean, default=False)
    uptime_kuma_push_url = Column(String, nullable=True)
    uptime_kuma_monitor_ids = Column(String, nullable=True) # comma-separated list of monitor IDs

class SMTPSettings(Base):
    __tablename__ = "smtp_settings"
    id = Column(Integer, primary_key=True, index=True)
    host = Column(String, default="")
    port = Column(Integer, default=587)
    username = Column(String, default="")
    password = Column(String, default="")
    from_email = Column(String, default="")
    from_name = Column(String, default="OmniBanner Alerts")
    enabled = Column(Boolean, default=False)

class Subscriber(Base):
    __tablename__ = "subscribers"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    active = Column(Boolean, default=True)

class OIDCSettings(Base):
    __tablename__ = "oidc_settings"
    id = Column(Integer, primary_key=True, index=True)
    provider_name = Column(String, default="Authentik")
    issuer_url = Column(String, default="")
    client_id = Column(String, default="")
    client_secret = Column(String, default="")
    enabled = Column(Boolean, default=False)

class UptimeKumaSettings(Base):
    __tablename__ = "uptime_kuma_settings"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, default="")
    username = Column(String, default="")
    password = Column(String, default="")
    enabled = Column(Boolean, default=False)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        config = db.query(SystemConfig).first()
        if not config:
            config = SystemConfig(setup_completed=False)
            db.add(config)
            db.commit()
    finally:
        db.close()
