from django.db import models

# Create your models here.
class AdminLeaderboardView(models.Model):
    class Meta:
        managed = False
        verbose_name = "Leaderboard"
        verbose_name_plural = "Leaderboard"