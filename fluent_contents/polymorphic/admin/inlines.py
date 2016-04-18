"""
Django Admin support for polymorphic inlines.

Each row in the inline can correspond with a different subclass.
"""
from functools import partial

from django.contrib.admin.options import InlineModelAdmin
from django.contrib.admin.utils import flatten_fieldsets
from django.forms import Media

from ..formsets import polymorphic_child_forms_factory, BasePolymorphicInlineFormSet, PolymorphicFormSetChild
from ..formsets.utils import add_media


class PolymorphicParentInlineModelAdmin(InlineModelAdmin):
    """
    A polymorphic inline, where each formset row can be a different form.

    Note that

    * Permissions are only checked on the base model.
    * The child inlines can't override the base model fields, only this parent inline can do that.
    * Child formset media is not yet processed.
    """
    formset = BasePolymorphicInlineFormSet

    #: The extra forms to show
    #: By default there are no 'extra' forms as the desired type is unknown.
    #: Instead, add each new item using JavaScript that first offers a type-selection.
    extra = 0

    #: Inlines for all model sub types that can be displayed in this inline.
    #: Each row is a :class:`PolymorphicChildInlineModelAdmin`
    child_inlines = ()

    def __init__(self, parent_model, admin_site):
        super(PolymorphicParentInlineModelAdmin, self).__init__(parent_model, admin_site)

        # While the inline is created per request, the 'request' object is not known here.
        # Hence, creating all child inlines unconditionally, without checking permissions.
        self.child_inline_instances = self.get_child_inline_instances()

        # Create a lookup table
        self._child_inlines_lookup = {}
        for child_inline in self.child_inline_instances:
            self._child_inlines_lookup[child_inline.model] = child_inline

    def get_child_inline_instances(self):
        """
        :rtype List[PolymorphicChildInlineModelAdmin]
        """
        instances = []
        for ChildInlineType in self.child_inlines:
            instances.append(ChildInlineType(parent_inline=self))
        return instances

    def get_child_inline_instance(self, model):
        """
        Find the child inline for a given model.

        :rtype: PolymorphicChildInlineModelAdmin
        """
        try:
            return self._child_inlines_lookup[model]
        except KeyError:
            raise ValueError("Model '{0}' not found in child_inlines".format(model.__name__))

    def get_formset(self, request, obj=None, **kwargs):
        """
        Construct the inline formset class.

        This passes all class attributes to the formset.

        :rtype: type
        """
        # Construct the FormSet class
        FormSet = super(PolymorphicParentInlineModelAdmin, self).get_formset(request, obj=obj, **kwargs)

        # Instead of completely redefining super().get_formset(), we use
        # the regular inlineformset_factory(), and amend that with our extra bits.
        # This is identical to what polymorphic_inlineformset_factory() does.
        FormSet.child_forms = polymorphic_child_forms_factory(
            formset_children=self.get_formset_children(request, obj=obj)
        )
        return FormSet

    def get_formset_children(self, request, obj=None):
        """
        The formset 'children' provide the details for all child models that are part of this formset.
        It provides a stripped version of the modelform/formset factory methods.
        """
        formset_children = []
        for child_inline in self.child_inline_instances:
            # TODO: the children can be limited here per request based on permissions.
            formset_children.append(child_inline.get_formset_child(request, obj=obj))
        return formset_children

    def get_fieldsets(self, request, obj=None):
        """
        Hook for specifying fieldsets.
        """
        if self.fieldsets:
            return self.fieldsets
        else:
            return []  # Avoid exposing fields to the child

    def get_fields(self, request, obj=None):
        if self.fields:
            return self.fields
        else:
            return []  # Avoid exposing fields to the child

    @property
    def media(self):
        # The media of the inline focuses on the admin settings,
        # whether to expose the scripts for filter_horizontal etc..
        # The admin helper exposes the inline + formset media.
        base_media = super(PolymorphicParentInlineModelAdmin, self).media
        all_media = Media()
        add_media(all_media, base_media)

        # Add all media of the child inline instances
        for child_instance in self.child_inline_instances:
            child_media = child_instance.media

            # Avoid adding the same media object again and again
            if child_media._css != base_media._css and child_media._js != base_media._js:
                add_media(all_media, child_media)

        return all_media


class PolymorphicChildInlineModelAdmin(InlineModelAdmin):
    """
    The child inline; which allows configuring the admin options
    for the child appearance.

    Note that not all options will be honored by the parent, notably the formset options:
    * :attr:`extra`
    * :attr:`min_num`
    * :attr:`max_num`

    The model form options however, will all be read.
    """
    formset_child = PolymorphicFormSetChild
    extra = 0  # TODO: currently unused for the children.

    def __init__(self, parent_inline):
        self.parent_inline = parent_inline
        super(PolymorphicChildInlineModelAdmin, self).__init__(parent_inline.parent_model, parent_inline.admin_site)

    def get_formset(self, request, obj=None, **kwargs):
        # The child inline is only used to construct the form,
        # and allow to override the form field attributes.
        # The formset is created by the parent inline.
        raise RuntimeError("The child get_formset() is not used.")

    def get_fields(self, request, obj=None):
        if self.fields:
            return self.fields

        # Standard Django logic, use the form to determine the fields.
        # The form needs to pass through all factory logic so all 'excludes' are set as well.
        # Default Django does: form = self.get_formset(request, obj, fields=None).form
        # Use 'fields=None' avoids recursion in the field autodetection.
        form = self.get_formset_child(request, obj, fields=None).get_form()
        return list(form.base_fields) + list(self.get_readonly_fields(request, obj))

    def get_formset_child(self, request, obj=None, **kwargs):
        """
        Return the formset child that the parent inline can use to represent us.

        :rtype: PolymorphicFormSetChild
        """
        # Similar to the normal get_formset(), the caller may pass fields to override the defaults settings
        # in the inline. In Django's GenericInlineModelAdmin.get_formset() this is also used in the same way,
        # to make sure the 'exclude' also contains the GFK fields.
        #
        # Hence this code is almost identical to InlineModelAdmin.get_formset()
        # and GenericInlineModelAdmin.get_formset()
        #
        # Transfer the local inline attributes to the formset child,
        # this allows overriding settings.
        if 'fields' in kwargs:
            fields = kwargs.pop('fields')
        else:
            fields = flatten_fieldsets(self.get_fieldsets(request, obj))

        if self.exclude is None:
            exclude = []
        else:
            exclude = list(self.exclude)

        exclude.extend(self.get_readonly_fields(request, obj))

        if self.exclude is None and hasattr(self.form, '_meta') and self.form._meta.exclude:
            # Take the custom ModelForm's Meta.exclude into account only if the
            # InlineModelAdmin doesn't define its own.
            exclude.extend(self.form._meta.exclude)

        #can_delete = self.can_delete and self.has_delete_permission(request, obj)
        defaults = {
            "form": self.form,
            "fields": fields,
            "exclude": exclude or None,
            "formfield_callback": partial(self.formfield_for_dbfield, request=request),
        }
        defaults.update(kwargs)

        # This goes through the same logic that get_formset() calls
        # by passing the inline class attributes to modelform_factory()
        FormSetChildClass = self.formset_child
        return FormSetChildClass(self.model, **defaults)
