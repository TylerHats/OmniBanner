import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def build_nice_email_html(app_name: str, primary_color: str, notice_text: str, start_time: str, end_time: str, target_sites: str = None) -> str:
    # Convert newlines to HTML breaks for formatting
    if notice_text:
        notice_text = notice_text.replace("\n", "<br>")
        
    targets_label = "All Sites"
    if target_sites:
        targets_label = ", ".join([site.strip() for site in target_sites.split(",") if site.strip()])

    
    # Emojis are supported out of the box because of UTF-8 encoding
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scheduled Notification: {app_name}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #f3f4f6;
            margin: 0;
            padding: 0;
            -webkit-font-smoothing: antialiased;
        }}
        .wrapper {{
            width: 100%;
            background-color: #f3f4f6;
            padding: 40px 0;
        }}
        .container {{
            max-width: 600px;
            margin: 0 auto;
            background-color: #ffffff;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }}
        .header {{
            background-color: {primary_color};
            padding: 30px 40px;
            text-align: center;
        }}
        .header h1 {{
            color: #ffffff;
            margin: 0;
            font-size: 24px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}
        .content {{
            padding: 40px;
            color: #374151;
            line-height: 1.6;
        }}
        .notice-box {{
            background-color: #fef2f2;
            border-left: 4px solid #ef4444;
            padding: 20px;
            border-radius: 6px;
            font-size: 16px;
            color: #b91c1c;
            margin-bottom: 24px;
            font-weight: 500;
        }}
        .details-table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 24px;
        }}
        .details-table td {{
            padding: 10px 0;
            border-bottom: 1px solid #e5e7eb;
            font-size: 14px;
        }}
        .details-table td.label {{
            color: #6b7280;
            width: 120px;
            font-weight: 600;
        }}
        .details-table td.value {{
            color: #111827;
        }}
        .footer {{
            background-color: #f9fafb;
            padding: 20px 40px;
            text-align: center;
            border-top: 1px solid #e5e7eb;
        }}
        .footer p {{
            margin: 0;
            font-size: 12px;
            color: #9ca3af;
        }}
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="container">
            <div class="header">
                <h1>{app_name} Notification</h1>
            </div>
            <div class="content">
                <p style="font-size: 16px; margin-top: 0;">Hello,</p>
                <p>We are writing to notify you of an upcoming scheduled event/maintenance window:</p>
                
                <div class="notice-box">
                    {notice_text}
                </div>
                
                <table class="details-table">
                    <tr>
                        <td class="label">Starts</td>
                        <td class="value">{start_time}</td>
                    </tr>
                    <tr>
                        <td class="label">Ends</td>
                        <td class="value">{end_time}</td>
                    </tr>
                    <tr>
                        <td class="label">Affected Sites</td>
                        <td class="value">{targets_label}</td>
                    </tr>
                </table>
                
                <p style="margin-bottom: 0;">Thank you for your patience and understanding.</p>
            </div>
            <div class="footer">
                <p>You received this email because you are subscribed to alerts for {app_name}.</p>
                <p style="margin-top: 5px;">&copy; {datetime.now().year} {app_name}. All rights reserved.</p>
            </div>
        </div>
    </div>
</body>
</html>
"""
    return html

def send_alert_email(smtp_config: dict, to_email: str, subject: str, html_content: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{smtp_config.get('from_name')} <{smtp_config.get('from_email')}>"
        msg["To"] = to_email
        
        part = MIMEText(html_content, "html", "utf-8")
        msg.attach(part)
        
        # Connect to server
        # For secure connections, we inspect port
        port = int(smtp_config.get("port", 587))
        host = smtp_config.get("host")
        username = smtp_config.get("username")
        password = smtp_config.get("password")
        
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10.0)
        else:
            server = smtplib.SMTP(host, port, timeout=10.0)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
        if username and password:
            server.login(username, password)
            
        server.sendmail(smtp_config.get("from_email"), to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"SMTP Error while sending email to {to_email}: {e}")
        return False
