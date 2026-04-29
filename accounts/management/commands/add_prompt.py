import logging
from django.utils.timezone import localtime
from django.utils.timezone import make_aware, now
from datetime import datetime, time, timedelta
from openai import OpenAI
import re
from django.db import transaction
from django.core.management.base import BaseCommand
from django.conf import settings
from accounts.models import GitCommit, Prompt, ScriptRunLog, UserProfile, FileChange


logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)


def get_ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


class Command(BaseCommand):
    help = "Generate prompt types and accuracy for Git commits using GPT."

    def add_arguments(self, parser):
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (bypass time restrictions)',
        )

    def handle(self, *args, **kwargs):
        logger.info("Prompt generation scheduler is running. Waiting for 22:15–22:20 IST...")

        now_ist = get_ist_now()
        today = now_ist.date()
        logger.info(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")

         

        # Check if we're in debug mode (force execution)
        debug_mode = kwargs.get('debug', False)
        
 
        
    
        if debug_mode:
            logger.info("Running in debug mode - bypassing time restrictions")
 

