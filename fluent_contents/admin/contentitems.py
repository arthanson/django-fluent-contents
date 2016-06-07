from django.utils.lru_cache import lru_cache
from polymorphic_tree.admin import PolymorphicMPTTParentModelAdmin, PolymorphicMPTTChildModelAdmin

from fluent_contents import extensions, appsettings
from fluent_contents.extensions import plugin_pool
from fluent_contents.forms import ContentItemForm
from fluent_contents.models import Placeholder, ContentItem, get_parent_language_code
from fluent_contents.polymorphic.admin import (
    PolymorphicParentGenericInlineModelAdmin,
    PolymorphicChildGenericInlineModelAdmin
)
from fluent_contents.polymorphic.formsets import BasePolymorphicGenericInlineFormSet
from fluent_contents.rendering.utils import add_media

# base fields in ContentItemForm
BASE_FIELDS = (
    'polymorphic_ctype',  # Added by BasePolymorphicModelFormSet
    'placeholder',
    'placeholder_slot',
    'parent_item',
    'parent_item_uid',
    'sort_order',
)


class BaseContentItemFormSet(BasePolymorphicGenericInlineFormSet):
    """
    Correctly save placeholder fields.
    """
    do_not_call_in_templates = True

    def __init__(self, *args, **kwargs):
        instance = kwargs['instance']
        if instance:
            self.current_language = get_parent_language_code(instance)
            if self.current_language and 'queryset' in kwargs:
                kwargs['queryset'] = kwargs['queryset'].filter(language_code=self.current_language)
        else:
            self.current_language = appsettings.FLUENT_CONTENTS_DEFAULT_LANGUAGE_CODE

        super(BaseContentItemFormSet, self).__init__(*args, **kwargs)
        self._deleted_placeholders = ()  # internal property, set by PlaceholderEditorAdmin
        self._placeholders_by_slot = {}

    def get_form_class(self, model):
        form = super(BaseContentItemFormSet, self).get_form_class(model)
        # Make sure that the automatically generated form does have all our common fields first.
        # This allows the admin CSS to pick .form-row:last-child

        # Redefine field order
        first_fields = list(ContentItemForm.base_fields.keys())
        field_order = first_fields + [f for f in form.base_fields.keys() if f not in first_fields]

        # Can't update collections.OrderedDict.__root easily, so recreate the object.
        base_fields = form.base_fields
        form.base_fields = base_fields.__class__((k, base_fields[k]) for k in field_order)
        return form

    def save_new(self, form, commit=True):
        # This is the moment to link the form 'placeholder_slot' field to a placeholder..
        # Notice that the PlaceholderEditorInline needs to be included before any ContentItemInline,
        # so it creates the Placeholder just before the ContentItem models are saved.
        #
        # In Django 1.3:
        # The save_new() function does not call form.save(), but constructs the model itself.
        # This allows it to insert the parent relation (ct_field / ct_fk_field) since they don't exist in the form.
        # It then calls form.cleaned_data to get all values, and use Field.save_form_data() to store them in the model instance.
        self._set_placeholder_id(form)

        # As save_new() completely circumvents form.save(), have to insert the language code here.
        instance = super(BaseContentItemFormSet, self).save_new(form, commit=False)
        instance.language_code = self.current_language

        if commit:
            instance.save()
            form.save_m2m()

        return instance

    def save_existing(self, form, instance, commit=True):
        if commit:
            self._set_placeholder_id(form)
        return form.save(commit=commit)

    def _set_placeholder_id(self, form):
        # If the slot is given, it overwrites whatever placeholder is selected.
        form_slot = form.cleaned_data['placeholder_slot']
        if form_slot:
            form_placeholder = form.cleaned_data['placeholder']   # could already be updated, or still point to previous placeholder.

            if not form_placeholder or form_placeholder.slot != form_slot:
                # Fetch the placeholder if needed, avoid having to repeat that query.
                try:
                    desired_placeholder = self._placeholders_by_slot[form_slot]
                except KeyError:
                    desired_placeholder = Placeholder.objects.parent(self.instance).get(slot=form_slot)
                    self._placeholders_by_slot[form_slot] = desired_placeholder

                form.cleaned_data['placeholder'] = desired_placeholder
                form.instance.placeholder = desired_placeholder

            elif form.instance.placeholder_id in self._deleted_placeholders:
                # ContentItem was in a deleted placeholder, and gets orphaned.
                form.cleaned_data['placeholder'] = None
                form.instance.placeholder = None

    @classmethod
    def get_default_prefix(cls):
        # Make output less verbose, easier to read, and less kB to transmit.
        opts = cls.model._meta
        return opts.object_name.lower()

    @property
    def type_name(self):
        """
        Return the classname of the model, this is mainly provided for templates.
        """
        #raise NotImplementedError()
        return self.model.__name__


class BaseContentItemAdminOptions(object):
    # Default admin settings
    form = ContentItemForm

    # overwritten by create_inline_for_plugin()
    name = None
    plugin = None
    extra_fieldsets = None
    type_name = None
    cp_admin_form_template = None
    cp_admin_init_template = None

    @property
    def media(self):
        media = super(BaseContentItemAdminOptions, self).media
        if self.plugin:
            media += self.plugin.media  # form fields first, plugin afterwards
        return media

    def get_fieldsets(self, request, obj=None):
        if not self.extra_fieldsets:
            # Let the admin auto detection do it's job. (means reading the form or formset class)
            return super(BaseContentItemAdminOptions, self).get_fieldsets(request, obj)
        else:
            return ((None, {'fields': BASE_FIELDS}),) + self.extra_fieldsets

    def formfield_for_dbfield(self, db_field, **kwargs):
        # Allow to use formfield_overrides using a fieldname too.
        # Avoids the major need to reroute formfield_for_dbfield() via the plugin.
        try:
            attrs = self.formfield_overrides[db_field.name]
            kwargs = dict(attrs, **kwargs)
        except KeyError:
            pass
        return super(BaseContentItemAdminOptions, self).formfield_for_dbfield(db_field, **kwargs)


class BaseContentItemParentInline(PolymorphicParentGenericInlineModelAdmin):
    """
    The ``InlineModelAdmin`` class used for all content items.
    """
    # inline settings
    ct_field = "parent_type"
    ct_fk_field = "parent_id"
    formset = BaseContentItemFormSet
    exclude = ('contentitem_ptr',)    # Fix django-polymorphic
    extra = 0  # defined on each child.
    ordering = ('sort_order',)
    template = 'admin/fluent_contents/contentitem/inline_container.html'
    is_fluent_editor_inline = True  # Allow admin templates to filter the inlines

    # Defined via get_content_item_inlines()
    plugins = []

    def __init__(self, parent_model, admin_site):
        super(BaseContentItemParentInline, self).__init__(parent_model, admin_site)
        self.verbose_name_plural = u'---- ContentItem Inline: %s' % (self.verbose_name_plural,)

    @property
    def media(self):
        # All admin media (e.g. for filter_horizontal)
        media = super(BaseContentItemParentInline, self).media

        # Plugin media is included afterwards
        for plugin in self.plugins:
            add_media(media, plugin.media)

        return media


class BaseContentItemChildInline(BaseContentItemAdminOptions, PolymorphicChildGenericInlineModelAdmin):
    """
    The inline for each child model.
    Most settings are placed here by the :func:`create_inline_for_plugin` function.
    """
    media = BaseContentItemAdminOptions.media  # optimize MediaDefiningClass
    exclude = ('contentitem_ptr',)  # Fix django-polymorphic

    # for completeness, because parent admin handles it all.
    ct_field = "parent_type"
    ct_fk_field = "parent_id"
    extra = 1


def get_content_item_inlines(plugins=None, parent_base=BaseContentItemParentInline, child_base=BaseContentItemChildInline):
    """
    Dynamically generate genuine django inlines for all registered content item types.
    When the `plugins` parameter is ``None``, all plugin inlines are returned.
    """
    if plugins is None:
        plugins = extensions.plugin_pool.get_plugins()

    child_inlines = []
    for plugin in plugins:
        child_inlines.append(
            create_inline_for_plugin(plugin, base=child_base)
        )

    # Construct a subclass of the Inline class,
    # that is configured with the static settings provided here.
    # Nowadays just one, that can spawn different form types.
    return [
        type('ContentItemParentInline', (parent_base,), {
            'model': ContentItem,
            'plugins': plugins,
            'child_inlines': child_inlines,
        })
    ]


def get_content_item_admin_options(plugin):
    """
    Get the settings for the Django admin class
    """
    # Avoid errors that are hard to trace
    if not isinstance(plugin, extensions.ContentPlugin):
        raise TypeError("get_content_item_inlines() expects to receive ContentPlugin instances, not {0}".format(plugin))

    COPY_FIELDS = (
        'form', 'raw_id_fields', 'filter_vertical', 'filter_horizontal',
        'radio_fields', 'prepopulated_fields', 'formfield_overrides', 'readonly_fields',
    )

    attrs = {
        '__module__': plugin.__class__.__module__,
        'model': plugin.model,

        # Add metadata properties for BaseContentItemAdminOptions and the template
        'name': plugin.verbose_name,
        'plugin': plugin,
        'type_name': plugin.type_name,
        'cp_admin_form_template': plugin.admin_form_template,
        'cp_admin_init_template': plugin.admin_init_template,
        'extra_fieldsets': tuple(plugin.fieldsets) if plugin.fieldsets else tuple(),
    }

    # Copy a restricted set of admin fields to the inline model too.
    for name in COPY_FIELDS:
        if getattr(plugin, name):
            attrs[name] = getattr(plugin, name)
    return attrs


@lru_cache()
def create_inline_for_plugin(plugin, base=BaseContentItemParentInline):
    """
    Create the admin inline for a ContentItem plugin.
    """
    attrs = get_content_item_admin_options(plugin)
    class_name = '%s_AutoChildInline' % plugin.model.__name__
    return type(class_name, (base,), attrs)


class ContentItemParentAdmin(PolymorphicMPTTParentModelAdmin):
    """
    Base admin to show a single ContentItem
    """
    base_model = ContentItem

    def get_child_models(self):
        results = []
        for plugin in plugin_pool.get_plugins():
            child_admin = create_admin_for_plugin(plugin)
            results.append((plugin.model, child_admin))
        return results


class ContentItemChildAdmin(BaseContentItemAdminOptions, PolymorphicMPTTChildModelAdmin):
    """
    Base admin to show a single ContentItem
    """
    base_model = ContentItem
    base_form = ContentItemForm
    media = BaseContentItemAdminOptions.media  # optimize MediaDefiningClass


@lru_cache()
def create_admin_for_plugin(plugin, base=ContentItemChildAdmin):
    """
    Create the admin instance for a ContentItem plugin.
    """
    attrs = get_content_item_admin_options(plugin)
    #attrs['base_form'] = attrs['form']
    class_name = '%s_ChildAdmin' % plugin.model.__name__
    return type(class_name, (base,), attrs)
