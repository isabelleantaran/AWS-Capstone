"""
RunReady — Intelligent Race Training Companion
Lambda function that sends personalized training notifications to runners.

Services used:
    - DynamoDB: Stores 8-week training plan
    - SNS: Sends email notifications
    - EventBridge: Triggers this function on schedule (Sun + Tue)

Author: Isabelle Antaran
"""

import json
import boto3
from datetime import datetime, timedelta
from decimal import Decimal

# ---- CONFIGURATION ----
# Update these values for your deployment
RUNNER_ID = "runner_001"
RUNNER_NAME = "Runner"  # Personalize when gifting
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:ACCOUNT_ID:RunReady-Notifications"  # Replace with your ARN
RACE_DATE = "2026-05-09"
RACE_DISTANCE_KM = 100
TABLE_NAME = "RunReadyTrainingPlan"

# ---- AWS CLIENTS ----
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')
table = dynamodb.Table(TABLE_NAME)


def get_current_week(race_date_str):
    """
    Calculate which training week we're in based on race date.
    
    The training plan is 8 weeks long, counting backward from race day.
    Week 1 starts exactly 8 weeks (56 days) before the race.
    
    Returns:
        int: Week number (1-8), or 0 if outside the training window.
    """
    today = datetime.now().date()
    race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
    training_start = race_date - timedelta(weeks=8)
    
    # Check if we're within the training window
    if today < training_start or today > race_date:
        return 0
    
    days_into_training = (today - training_start).days
    week_number = (days_into_training // 7) + 1
    return min(week_number, 8)


def get_message_type():
    """
    Determine which message to send based on the current day of week.
    
    Sunday (weekday 6) = weekly recap message
    Tuesday (weekday 1) = pre-long-run guidance
    
    Returns:
        str or None: Message type key matching DynamoDB attribute name,
                     or None if today is not a notification day.
    """
    day_of_week = datetime.now().weekday()
    if day_of_week == 6:  # Sunday
        return "sunday_message"
    elif day_of_week == 1:  # Tuesday
        return "tuesday_message"
    else:
        return None


def get_training_plan(week_number):
    """
    Fetch the training plan for a specific week from DynamoDB.
    
    Args:
        week_number (int): Training week (1-8)
    
    Returns:
        dict: Training plan item with mileage targets, pace guidance,
              terrain notes, and pre-written messages.
    """
    response = table.get_item(
        Key={
            'runner_id': RUNNER_ID,
            'week_number': int(week_number)
        }
    )
    return response.get('Item')


def format_email(plan, message_type, week_number):
    """
    Build a formatted email with training details and phase-appropriate messaging.
    
    The email includes:
    - Header with week number, phase, and countdown
    - Personalized training message (Sunday recap or Tuesday long-run guidance)
    - Weekly mileage targets broken down by run type
    - Pace guidance and terrain/elevation notes
    
    Args:
        plan (dict): Training plan from DynamoDB
        message_type (str): 'sunday_message' or 'tuesday_message'
        week_number (int): Current training week
    
    Returns:
        tuple: (email_body, subject_line)
    """
    phase_labels = {
        'peak': 'PEAK TRAINING',
        'pre-taper': 'PRE-TAPER',
        'taper': 'TAPER',
        'race-week': 'RACE WEEK'
    }
    
    race_date = datetime.strptime(RACE_DATE, "%Y-%m-%d").date()
    today = datetime.now().date()
    days_left = (race_date - today).days
    
    phase = phase_labels.get(plan['phase'], plan['phase'].upper())
    message = plan[message_type]
    
    subject_prefix = "Sunday Recap" if message_type == "sunday_message" else "Long Run Day"
    
    email_body = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNREADY - {subject_prefix.upper()}
  Week {week_number} of 8 | Phase: {phase}
  {days_left} days until race day ({RACE_DISTANCE_KM}km)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Hey {RUNNER_NAME},

{message}

━━ THIS WEEK'S NUMBERS ━━

  Total Mileage Target: {plan['total_mileage_target']} miles
  Long Run: {plan['long_run_miles']} miles
  Mid Runs: {plan['mid_run_miles']} miles
  Easy Runs: {plan['easy_run_miles']} miles

━━ PACE GUIDANCE ━━

  {plan['pace_guidance']}

━━ TERRAIN & ELEVATION ━━

  {plan['terrain_focus']}
  {plan['elevation_note']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Powered by RunReady | Built with AWS
  Lambda + DynamoDB + SNS + EventBridge
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return email_body, f"RunReady | {subject_prefix} - Week {week_number} ({phase})"


def lambda_handler(event, context):
    """
    Main entry point — triggered by EventBridge on Sundays and Tuesdays.
    
    Flow:
        1. Calculate current training week from race date
        2. Check if today is a notification day (Sun or Tue)
        3. Fetch the week's training plan from DynamoDB
        4. Format and send the notification via SNS
    
    The function also accepts a manual override via test events:
        {"message_type": "sunday_message"} or {"message_type": "tuesday_message"}
    This allows testing on any day of the week.
    
    Args:
        event (dict): EventBridge event or manual test event
        context: Lambda context object
    
    Returns:
        dict: Status code and result message
    """
    
    # Step 1: What week are we in?
    week_number = get_current_week(RACE_DATE)
    
    if week_number == 0:
        return {
            'statusCode': 200,
            'body': json.dumps('Outside training window - no notification sent.')
        }
    
    # Step 2: Is today a notification day?
    message_type = get_message_type()
    
    # Allow manual override via test event for debugging
    if message_type is None:
        message_type = event.get('message_type', None)
    
    if message_type is None:
        return {
            'statusCode': 200,
            'body': json.dumps(f'Today is not a notification day. Current week: {week_number}')
        }
    
    # Step 3: Get the training plan from DynamoDB
    plan = get_training_plan(week_number)
    
    if not plan:
        return {
            'statusCode': 404,
            'body': json.dumps(f'No training plan found for week {week_number}')
        }
    
    # Step 4: Format and send via SNS
    email_body, subject = format_email(plan, message_type, week_number)
    
    response = sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Message=email_body,
        Subject=subject
    )
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f'Notification sent for Week {week_number} ({message_type})',
            'sns_message_id': response['MessageId']
        })
    }
