"""Email alert service for security notifications.

Sends alerts to users and admin via Gmail SMTP when suspicious activity detected.
Uses environment variables for configuration:
  - ALERT_EMAIL_SENDER: Gmail address (sender)
  - ALERT_EMAIL_PASSWORD: Gmail App Password (not regular password)
  - ADMIN_EMAIL: Admin Gmail address for alerts
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

# Configuration from environment
SENDER_EMAIL = os.environ.get("ALERT_EMAIL_SENDER", "").strip()
# Gmail app passwords are often displayed with spaces; strip whitespace to avoid auth failures.
_RAW_SENDER_PASSWORD = os.environ.get("ALERT_EMAIL_PASSWORD", "")
SENDER_PASSWORD = _RAW_SENDER_PASSWORD.replace(" ", "").strip()
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip()
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(to_address: str, subject: str, body: str, is_html: bool = False) -> bool:
    """Send an email alert.
    
    Args:
        to_address: Recipient email address
        subject: Email subject
        body: Email body (plain text or HTML)
        is_html: Whether body is HTML
        
    Returns:
        True if successful, False otherwise
    """
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning(
            "Email service not configured (ALERT_EMAIL_SENDER/ALERT_EMAIL_PASSWORD). "
            "Skipping email to %s",
            to_address,
        )
        return False
    
    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = to_address
        
        # Add body
        mime_type = "html" if is_html else "plain"
        msg.attach(MIMEText(body, mime_type))
        
        # Send via Gmail SMTP
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        
        logger.info("Email sent to %s: %s", to_address, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_address, e)
        return False


def alert_suspicious_activity(telegram_id: int, user_email: str | None, action_type: str, count: int) -> bool:
    """Send alert for suspicious activity (too many deletes, moves, downloads).
    
    Sends email to user (if email configured) and admin.
    """
    subject = f"🚨 Security Alert: Suspicious Activity Detected (User {telegram_id})"
    
    body = f"""
Security Alert - Suspicious Activity Detected

User ID: {telegram_id}
Activity Type: {action_type.upper()}
Action Count: {count} in 5 minutes
Timestamp: {__import__('datetime').datetime.now().isoformat()}

ACTION TAKEN:
Your Google Drive access has been REVOKED as a precaution.
Please log in again with /login to reconnect safely.

If you did not authorize this activity:
1. Change your Google password immediately
2. Review your Google Account security settings
3. Contact support

For more details or to restore access, reply to this email.
"""
    
    html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #d32f2f;">🚨 Security Alert</h2>
    <p><strong>Suspicious Activity Detected</strong></p>
    
    <table style="border-collapse: collapse; width: 100%; max-width: 500px; margin: 20px 0;">
      <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>User ID:</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{telegram_id}</td>
      </tr>
      <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Activity Type:</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{action_type.upper()}</td>
      </tr>
      <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Count (5 min):</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{count} actions</td>
      </tr>
      <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Status:</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd; color: #d32f2f; font-weight: bold;">REVOKED</td>
      </tr>
    </table>
    
    <div style="background-color: #fff3e0; padding: 15px; border-left: 4px solid #ff9800; margin: 20px 0;">
      <strong>Action Required:</strong><br>
      Your Google Drive access has been automatically revoked to protect your account.
      <br><br>
      To reconnect:
      <ol>
        <li>Send <code>/login</code> to re-authenticate</li>
        <li>Verify your Google account security settings</li>
        <li>Check if you authorized this activity</li>
      </ol>
    </div>
    
    <p style="color: #999; font-size: 12px;">
      This is an automated security alert. Do not reply to this email.
      <br>For support, contact the bot administrator.
    </p>
  </body>
</html>
"""
    
    success = True
    
    # Send to user if email is configured
    if user_email:
        if not send_email(user_email, subject, html_body, is_html=True):
            success = False
    
    # Always send to admin
    admin_subject = f"[ADMIN] Security Alert: User {telegram_id} - {action_type.upper()}"
    admin_body = f"""
SECURITY ALERT - ADMIN NOTIFICATION

User: {telegram_id}
Activity: {action_type.upper()} ({count} times in 5 minutes)
User Email: {user_email or 'NOT SET'}

ACTION TAKEN:
- All users' Google Drive tokens have been REVOKED
- User notified via Telegram and email (if configured)

RECOMMENDED ACTIONS:
1. Review audit logs for this user
2. Check for other suspicious patterns
3. Contact user to verify if authorized

Database Query:
SELECT * FROM audit_log WHERE telegram_id = {telegram_id} ORDER BY created_at DESC;
SELECT * FROM security_alerts WHERE telegram_id = {telegram_id} ORDER BY created_at DESC;
"""
    
    if ADMIN_EMAIL:
        if not send_email(ADMIN_EMAIL, admin_subject, admin_body, is_html=False):
            success = False
    
    return success


def alert_token_revoked(telegram_id: int, user_email: str | None, reason: str) -> bool:
    """Notify user that their token was revoked."""
    subject = f"🔐 Google Drive Access Revoked (User {telegram_id})"
    
    body = f"""
Google Drive Access Revoked

Your Google Drive access has been revoked.

Reason: {reason}

If this was not authorized:
1. Change your Google password immediately
2. Enable 2-Step Verification on your Google account
3. Review recent sign-in activity
4. Contact support if you need help

To restore access, send /login to the bot.
"""
    
    html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #1976d2;">🔐 Google Drive Access Revoked</h2>
    
    <div style="background-color: #e3f2fd; padding: 15px; border-left: 4px solid #1976d2; margin: 20px 0;">
      <p><strong>Your Google Drive access has been revoked.</strong></p>
      <p><strong>Reason:</strong> {reason}</p>
    </div>
    
    <h3>What You Should Do:</h3>
    <ol>
      <li><strong>Change Your Google Password</strong> - Go to myaccount.google.com/security</li>
      <li><strong>Enable 2-Step Verification</strong> - Add an extra layer of security</li>
      <li><strong>Review Sign-In Activity</strong> - Check if you recognize recent logins</li>
      <li><strong>Restore Access</strong> - Send <code>/login</code> to the bot when ready</li>
    </ol>
    
    <p style="color: #999; font-size: 12px;">
      Need help? Contact the bot administrator.
    </p>
  </body>
</html>
"""
    
    success = True
    if user_email:
        if not send_email(user_email, subject, html_body, is_html=True):
            success = False
    
    return success
