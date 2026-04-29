from django.shortcuts import redirect
from django.utils import timezone
from allauth.socialaccount.models import SocialToken
class GitHubTokenExpiryCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                token_obj = SocialToken.objects.get(account__user=request.user, account__provider='github')
                if token_obj.expires_at and timezone.now() >= token_obj.expires_at:
                    return redirect('login')  # Redirect to GitHub login page
            except SocialToken.DoesNotExist:
                pass
        return self.get_response(request)