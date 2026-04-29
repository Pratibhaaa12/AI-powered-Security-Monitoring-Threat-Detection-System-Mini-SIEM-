from django import forms
from .models import Comment, UserProfile

class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3, "placeholder": "Add your comment..."}),
        }




class ProfileForm(forms.ModelForm):
    aliases = forms.CharField(required=False, help_text="Comma separated aliases (names or emails)")

    class Meta:
        model = UserProfile
        fields = ["aliases"]

    def clean_aliases(self):
        data = self.cleaned_data["aliases"]
        if data:
            return [a.strip() for a in data.split(",") if a.strip()]
        return []
