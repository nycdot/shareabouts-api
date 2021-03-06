from django.contrib.gis.db import models
from django.db.models.signals import post_save
from .. import utils
from .core import CacheClearingModel
from .core import DataSet
from .mixins import CloneableModelMixin

class DataPermissionManager (models.Manager):
    use_for_related_fields = True

    @utils.memo
    def all_permissions(self):
        return self.all()


class DataPermission (CloneableModelMixin, CacheClearingModel, models.Model):
    """
    Rules for what permissions a given authentication method affords.
    """
    submission_set = models.CharField(max_length=128, blank=True, help_text='Either the name of a submission set (e.g., "comments"), or "places". Leave blank to refer to all things.')
    can_retrieve = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_update = models.BooleanField(default=False)
    can_destroy = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(blank=True)

    objects = DataPermissionManager()

    class Meta:
        abstract = True
        ordering = ('priority',)

    def parent():
        def fget(self): return getattr(self, self.parent_attr)
        def fset(self, value): setattr(self, self.parent_attr, value)
        return locals()
    parent = property(**parent())

    def siblings():
        def fget(self): return self.parent.permissions.all()
        return locals()
    siblings = property(**siblings())

    def dataset():
        def fget(self): return self.parent.dataset
        return locals()
    dataset = property(**dataset())

    def abilities(self):
        abilities = []
        if self.can_create: abilities.append('create')
        if self.can_retrieve: abilities.append('retrieve')
        if self.can_update: abilities.append('update')
        if self.can_destroy: abilities.append('destroy')

        things = self.submission_set if self.submission_set.strip() not in ('', '*') else 'anything'

        if abilities:
            if len(abilities) > 1: abilities[-1] = 'or ' + abilities[-1]
            return 'can ' + ', '.join(abilities) + ' ' + things
        else:
            return 'can not create, retrieve, update, or destroy ' + things + ' at all'

    def clear_instance_cache(self):
        return self.dataset.clear_instance_cache()

    def save(self, *args, **kwargs):
        if self.priority is None:
            try:
                lowest = self.siblings.order_by('-priority')[0]
                self.priority = lowest.priority + 1
            except IndexError:
                self.priority = 0

        return super(DataPermission, self).save(*args, **kwargs)


class DataSetPermission (DataPermission):
    dataset = models.ForeignKey('DataSet', related_name='permissions')
    parent_attr = 'dataset'

    class Meta:
        app_label = 'sa_api_v2'

    def __unicode__(self):
        return '%s %s' % ('submitters', self.abilities())


class GroupPermission (DataPermission):
    group = models.ForeignKey('Group', related_name='permissions')
    parent_attr = 'group'

    class Meta:
        app_label = 'sa_api_v2'

    def __unicode__(self):
        return '%s %s' % (self.group, self.abilities())


class KeyPermission (DataPermission):
    key = models.ForeignKey('sa_api_v2.ApiKey', related_name='permissions')
    parent_attr = 'key'

    class Meta:
        app_label = 'sa_api_v2'

    def __unicode__(self):
        return 'submitters %s' % (self.abilities(),)


class OriginPermission (DataPermission):
    origin = models.ForeignKey('sa_api_v2.Origin', related_name='permissions')
    parent_attr = 'origin'

    class Meta:
        app_label = 'sa_api_v2'

    def __unicode__(self):
        return 'submitters %s' % (self.abilities(),)


def create_data_permissions(sender, instance, created, **kwargs):
    """
    Create a default permission instance for a new dataset.
    """
    if created:
        DataSetPermission.objects.create(dataset=instance, submission_set='*',
            can_retrieve=True, can_create=False, can_update=False, can_destroy=False)
post_save.connect(create_data_permissions, sender=DataSet, dispatch_uid="dataset-create-permissions")


def any_allow(permissions, do_action, submission_set):
    """
    Check whether any of the data permissions in the managed set allow the
    action on a submission set with the given name.
    """
    for permission in permissions:
        if (permission.submission_set in (submission_set, '*')
            and getattr(permission, 'can_' + do_action, False)):
            return True
    return False

def check_data_permission(user, client, do_action, dataset, submission_set):
    """
    Check whether the given user has permission on the submission_set in
    the context of the given client (e.g., an API key or an origin).
    """
    if do_action not in ('retrieve', 'create', 'update', 'destroy'):
        raise ValueError

    if user and user.is_superuser:
        return True

    # Owner can do anything
    if user and dataset and user.id == dataset.owner_id:
        return True

    # Start with the dataset permission
    if dataset and any_allow(dataset.permissions.all(), do_action, submission_set):
        return True

    # Then the client permission
    if client is not None:
        if (client.dataset == dataset and
            any_allow(client.permissions.all(), do_action, submission_set)):
            return True

    # Next, check the user's groups
    if user is not None and user.is_authenticated():
        for group in user._groups.all():
            if (dataset and group.dataset_id == dataset.id and
                any_allow(group.permissions.all(), do_action, submission_set)):
                return True

    return False

