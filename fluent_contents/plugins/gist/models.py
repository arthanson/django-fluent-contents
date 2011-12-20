from django.db import models
from django.utils.translation import ugettext_lazy as _
from fluent_contents.models import ContentItem

class GistItem(ContentItem):
    """
    A reference to a gist item (gist.github.com) that is rendered as source code.
    """
    gist_id = models.IntegerField(_("Gist number"), help_text=_('Go to <a href="https://gist.github.com/" target="_blank">https://gist.github.com/</a> and copy the number of the Gist snippet you want to display.'))
    filename = models.CharField(_("Gist filename"), max_length=128, blank=True, help_text=_('Leave the filename empty to display all files in the Gist.'))

    class Meta:
        verbose_name = _('GitHub Gist snippet')
        verbose_name_plural = _('GitHub Gist snippets')