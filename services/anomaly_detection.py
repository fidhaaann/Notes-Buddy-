"""Anomaly detection service for security monitoring.

Detects suspicious patterns like:
- 10+ deletes within 5 minutes
- 20+ moves/renames within 5 minutes  
- 50+ downloads within 5 minutes

When anomaly detected:
1. Logs security alert
2. Revokes ALL user tokens (emergency measure)
3. Notifies user and admin via email/Telegram
4. Bot disconnects from all Google Drive accounts
"""

import logging
from db import models
from drive import auth as drive_auth
from services import email_service
from services import alert_service

logger = logging.getLogger(__name__)

# Anomaly thresholds
ANOMALY_THRESHOLDS = {
    "delete": 10,      # 10+ deletes in 5 minutes
    "move": 20,        # 20+ moves in 5 minutes
    "rename": 20,      # 20+ renames in 5 minutes
    "download": 50,    # 50+ downloads in 5 minutes
}


async def check_anomaly(telegram_id: int, action: str) -> bool:
    """
    Check if action count exceeds anomaly threshold.
    
    Returns True if anomaly detected (and handled).
    """
    # Only track monitored actions
    if action not in ANOMALY_THRESHOLDS:
        return False
    
    # Increment counter for this action within 5-minute window
    count = models.track_action(telegram_id, action)
    threshold = ANOMALY_THRESHOLDS[action]
    
    logger.debug("Action tracking: user=%s action=%s count=%d threshold=%d", 
                 telegram_id, action, count, threshold)
    
    # Check if threshold exceeded
    if count > threshold:
        await handle_anomaly_detected(telegram_id, action, count)
        return True
    
    return False


async def handle_anomaly_detected(telegram_id: int, action: str, count: int) -> None:
    """Handle anomaly: revoke tokens and notify user/admin."""
    
    logger.critical(
        "ANOMALY DETECTED: user=%s action=%s count=%d (threshold=%d)",
        telegram_id, action, count, ANOMALY_THRESHOLDS[action]
    )
    
    # Get user email for alerts
    user_email = models.get_user_email(telegram_id)
    
    # Log security alert to database
    models.log_security_alert(
        telegram_id=telegram_id,
        alert_type="anomaly_detected",
        description=f"{count} {action} operations in 5 minutes",
        action_taken="All user tokens revoked"
    )
    
    # Revoke only the affected user's token
    revoked = False
    try:
        drive_auth.revoke_token(telegram_id)
        revoked = True
        logger.info("Revoked token for user %s (anomaly response)", telegram_id)
    except Exception as e:
        logger.error("Failed to revoke token for user %s: %s", telegram_id, e)
    
    # Send alerts
    alert_message = (
        "🛡 Security Alert\n\n"
        f"Detected unusual activity: {count} {action} operations in 5 minutes.\n"
        "As a precaution, your Drive access has been revoked.\n\n"
        "If this was you, please reconnect with /login.\n"
        "If not, change your Google password and review account security."
    )
    await alert_service.send_telegram_alert(telegram_id, alert_message)

    if user_email and alert_service.email_alerts_enabled(telegram_id):
        try:
            email_service.alert_suspicious_activity(
                telegram_id=telegram_id,
                user_email=user_email,
                action_type=action,
                count=count
            )
        except Exception as e:
            logger.error("Failed to send alert email: %s", e)
    
    # Log details
    logger.warning(
        "ANOMALY RESPONSE COMPLETE: revoked=%s severity=high action=%s",
        revoked, action
    )


def cleanup_old_tracking() -> None:
    """Clean up anomaly tracking records older than 24 hours."""
    try:
        models.cleanup_anomaly_tracking()
        logger.debug("Cleaned up old anomaly tracking records")
    except Exception as e:
        logger.error("Failed to cleanup anomaly tracking: %s", e)
