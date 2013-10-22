from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.gis.geos import GEOSGeometry, Point
from django.core import cache as django_cache
from django.core.urlresolvers import reverse
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.test.utils import override_settings
from rest_framework import (views, permissions, mixins, authentication,
                            generics, exceptions, status)
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer, JSONPRenderer, BrowsableAPIRenderer
from rest_framework.request import Request
from rest_framework.exceptions import APIException
from social.apps.django_app import views as social_views
from mock import patch
from . import models
from . import serializers
from . import utils
from . import renderers
from . import parsers
from . import apikey
from . import cors
from . import utils
from .params import (INCLUDE_INVISIBLE_PARAM, INCLUDE_PRIVATE_PARAM,
    INCLUDE_SUBMISSIONS_PARAM, NEAR_PARAM, FORMAT_PARAM, PAGE_PARAM,
    PAGE_SIZE_PARAM, CALLBACK_PARAM)
from functools import wraps
from itertools import groupby
from collections import defaultdict
from urlparse import urlparse, urljoin
from urllib import urlencode
import re
import ujson as json
import logging

logger = logging.getLogger('sa_api_v2.views')

###############################################################################
#
# Permissions
# -----------
#


def is_owner(user, request):
    username = getattr(user, 'username', None)
    allowed_username = getattr(request, 'allowed_username', None)
    # XXX Watch out when mocking users in tests: bool(mock.Mock()) is True
    return (username and allowed_username == username)


def is_apikey_auth(auth):
    return isinstance(auth, apikey.models.ApiKey)


def is_origin_auth(auth):
    return isinstance(auth, basestring) and auth.startswith('origin')


def is_really_logged_in(user, request):
    auth = getattr(request, 'auth', None)
    return (user.is_authenticated() and
            not is_apikey_auth(auth) and
            not is_origin_auth(auth))


class IsOwnerOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        """
        Allows only superusers or the user named by
        `request.allowed_username` to write.

        (If the view has no such attribute, assumes not allowed)
        """
        if (request.method in permissions.SAFE_METHODS or
            is_owner(request.user, request) or request.user.is_superuser
            or (hasattr(request, 'client') and
                hasattr(request.client, 'owner') and
                is_owner(request.client.owner, request))):
            return True
        return False


class IsLoggedInOwnerOrPublicDataOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        """
        Disallows any request for public data from a user authenticated
        by API key.

        For protecting views related to API keys that should require
        'real' authentication, to avoid users abusing one API key to
        obtain others.
        """
        private_data_flags = [INCLUDE_PRIVATE_PARAM, INCLUDE_INVISIBLE_PARAM]
        if not any([flag in request.GET for flag in private_data_flags]):
            return True

        if not is_really_logged_in(request.user, request):
            return False

        if is_owner(request.user, request) or request.user.is_superuser:
            return True

        return False


class IsLoggedInAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        if not is_really_logged_in(request.user, request):
            return False

        if request.user.is_superuser:
            return True

        return False


###############################################################################
#
# View Mixins
# -----------
#

class ShareaboutsAPIRequest (Request):
    """
    A subclass of the DRF Request that allows dual authentication as a user
    and an application (client) at the same time.
    """

    def __init__(self, request, parsers=None, authenticators=None,
                 client_authenticators=None, negotiator=None, 
                 parser_context=None):
        super(ShareaboutsAPIRequest, self).__init__(request,
            parsers=parsers, authenticators=authenticators,
            negotiator=negotiator, parser_context=parser_context)
        self.client_authenticators = client_authenticators

    @property
    def client(self):
        """
        Returns the client associated with the current request, as authenticated
        by the authentication classes provided to the request.
        """
        if not hasattr(self, '_client'):
            self._authenticate_client()
        return self._client

    @client.setter
    def client(self, value):
        """
        Sets the client on the current request.
        """
        self._client = value

    @property
    def client_auth(self):
        """
        Returns any non-client authentication information associated with the
        request, such as an authentication token.
        """
        if not hasattr(self, '_client_auth'):
            self._authenticate_client()
        return self._auth

    @client_auth.setter
    def client_auth(self, value):
        """
        Sets any non-client authentication information associated with the
        request, such as an authentication token.
        """
        self._client_auth = value

    @property
    def successful_authenticator(self):
        """
        Return the instance of the authentication instance class that was used
        to authenticate the request, or `None`.
        """
        authenticator = super(ShareaboutsAPIRequest, self).successful_authenticator

        if not authenticator:
            if not hasattr(self, '_client_authenticator'):
                self._authenticate_client()
            authenticator = self._client_authenticator

        return authenticator

    def _authenticate_client(self):
        """
        Attempt to authenticate the request using each authentication instance
        in turn.
        Returns a three-tuple of (authenticator, client, client_authtoken).
        """
        for authenticator in self.client_authenticators:
            try:
                client_auth_tuple = authenticator.authenticate(self)
            except exceptions.APIException:
                self._client_not_authenticated()
                raise

            if client_auth_tuple is not None:
                self._client_authenticator = authenticator
                self._client, self._client_auth = client_auth_tuple
                return

        self._client_not_authenticated()

    def _client_not_authenticated(self):
        """
        Generate a three-tuple of (authenticator, client, authtoken), representing
        an unauthenticated request.
        """
        self._client_authenticator = None
        self._client = None
        self._client_auth = None


class ClientAuthenticationMixin (object):
    """
    A view mixin that uses a ShareaboutsAPIRequest instead of a conventional
    DRF Request object.
    """

    def get_client_authenticators(self):
        """
        Instantiates and returns the list of client authenticators that this view can use.
        """
        return [auth() for auth in self.client_authentication_classes]

    def initialize_request(self, request, *args, **kwargs):
        """
        Override the initialize_request method in the base APIView so that we
        can use a custom request object.
        """
        parser_context = self.get_parser_context(request)

        return ShareaboutsAPIRequest(request,
            parsers=self.get_parsers(),
            authenticators=self.get_authenticators(),
            client_authenticators=self.get_client_authenticators(),
            negotiator=self.get_content_negotiator(),
            parser_context=parser_context)


class CorsEnabledMixin (object):
    """
    A view that puts Access-Control headers on the response.
    """

    def finalize_response(self, request, response, *args, **kwargs):
        response = super(CorsEnabledMixin, self).finalize_response(request, response, *args, **kwargs)

        # Allow AJAX requests from anywhere, knowing that we're protected
        # by origin-based client authentication.
        response['Access-Control-Allow-Origin'] = '*'
        return response


class FilteredResourceMixin (object):
    """
    A view mixin that filters queryset of ModelWithDataBlob results based on
    the URL query parameters.
    """
    def get_queryset(self):
        queryset = super(FilteredResourceMixin, self).get_queryset()

        # Filter by any provided primary keys
        pk_list = self.kwargs.get('pk_list', None)
        if pk_list is not None:
            pk_list = pk_list.split(',')
            queryset = queryset.filter(pk__in=pk_list)

        # These filters will have been applied when constructing the queryset
        special_filters = set([FORMAT_PARAM, PAGE_PARAM, PAGE_SIZE_PARAM(),
            INCLUDE_SUBMISSIONS_PARAM, INCLUDE_PRIVATE_PARAM,
            INCLUDE_INVISIBLE_PARAM, NEAR_PARAM, CALLBACK_PARAM(self)])

        for key, values in self.request.GET.iterlists():
            if key not in special_filters:
                # Filter!
                for obj in queryset:
                    if hasattr(obj, key):
                        if getattr(obj, key) not in values:
                            queryset = queryset.exclude(pk=obj.pk)
                    else:
                        # Is it in the data blob?
                        data = json.loads(obj.data)
                        if key not in data or data[key] not in values:
                            queryset = queryset.exclude(pk=obj.pk)

        return queryset


class LocatedResourceMixin (object):
    """
    A view mixin that orders queryset results by distance from a geometry, if
    requested.
    """
    def get_queryset(self):
        queryset = super(LocatedResourceMixin, self).get_queryset()

        if NEAR_PARAM in self.request.GET:
            try:
                reference = utils.to_geom(self.request.GET[NEAR_PARAM])
            except ValueError:
                raise QueryError(detail='Invalid parameter for "%s": %r' % (NEAR_PARAM, self.request.GET[NEAR_PARAM]))
            queryset = queryset.distance(reference).order_by('distance')

        return queryset


class OwnedResourceMixin (ClientAuthenticationMixin, CorsEnabledMixin):
    """
    A view mixin that retrieves the username of the resource owner, as provided
    in the URL, and stores it on the request object.

    Permissions
    -----------
    Owned resource views are available for reading to all users, and available
    for writing to the owner, logged in by key or directly. Only the owner
    logged in directly is allowed to read invisible resources or private data
    attributes on visible resources.
    """
    renderer_classes = (JSONRenderer, JSONPRenderer, BrowsableAPIRenderer, renderers.PaginatedCSVRenderer)
    parser_classes = (JSONParser, FormParser, MultiPartParser)
    permission_classes = (IsOwnerOrReadOnly, IsLoggedInOwnerOrPublicDataOnly)
    authentication_classes = (authentication.BasicAuthentication, authentication.SessionAuthentication)
    client_authentication_classes = (apikey.auth.ApiKeyAuthentication, cors.auth.OriginAuthentication)

    owner_username_kwarg = 'owner_username'
    dataset_slug_kwarg = 'dataset_slug'

    def dispatch(self, request, *args, **kwargs):
        request.allowed_username = kwargs[self.owner_username_kwarg]

        # Make sure the request has access to the dataset, since client
        # authentication must check against it.
        request.get_dataset = self.get_dataset

        return super(OwnedResourceMixin, self).dispatch(request, *args, **kwargs)

    def get_submitter(self):
        user = self.request.user
        return user if user.is_authenticated() else None

    def get_owner(self):
        owner_username = self.kwargs[self.owner_username_kwarg]
        owner = get_object_or_404(models.User, username=owner_username)
        return owner

    def get_dataset(self):
        owner = self.get_owner()

        dataset_slug = self.kwargs[self.dataset_slug_kwarg]
        dataset = get_object_or_404(models.DataSet, slug=dataset_slug, owner=owner)
        return dataset

    def is_verified_object(self, obj, ObjType=None):
        # Get the instance parameters from the cache
        ObjType = ObjType or self.model
        params = ObjType.cache.get_cached_instance_params(obj.pk, lambda: obj)

        # Make sure that the instance parameters match what we got in the URL.
        # We do not want to risk assuming a user owns a place, for example, just
        # because their username is in the URL.
        for attr in self.kwargs:
            if attr in params and unicode(self.kwargs[attr]) != unicode(params[attr]):
                return False

        return True

    def verify_object(self, obj, ObjType=None):
        # If the object is invisible, check that include_invisible is on
        if not getattr(obj, 'visible', True):
            if INCLUDE_INVISIBLE_PARAM not in self.request.GET:
                raise QueryError(detail='You must explicitly request invisible resources with the "include_invisible" parameter.')

        if not self.is_verified_object(obj, ObjType):
            raise Http404


class CachedResourceMixin (object):
    @property
    def cache_prefix(self):
        return self.request.path

    def get_cache_prefix(self):
        return self.cache_prefix

    def get_cache_metakey(self):
        prefix = self.cache_prefix
        return prefix + '_keys'

    def dispatch(self, request, *args, **kwargs):
        # Only do the cache for GET, OPTIONS, or HEAD method.
        if request.method.upper() not in permissions.SAFE_METHODS:
            return super(CachedResourceMixin, self).dispatch(request, *args, **kwargs)

        self.request = request

        # Check whether the response data is in the cache.
        key = self.get_cache_key(request, *args, **kwargs)
        response_data = django_cache.cache.get(key) or None

        # Also check whether the request cache key is managed in the cache.
        # This is important, because if it's not managed, then we'll never
        # know when to invalidate it. If it's not managed we should just
        # assume that it's invalid.
        metakey = self.get_cache_metakey()
        keyset = django_cache.cache.get(metakey) or set()

        if (response_data is not None) and (key in keyset):
            cached_response = self.respond_from_cache(response_data)
            handler_name = request.method.lower()

            def cached_handler(*args, **kwargs):
                return cached_response

            # Patch the HTTP method
            with patch.object(self, handler_name, new=cached_handler):
                response = super(CachedResourceMixin, self).dispatch(request, *args, **kwargs)
        else:
            response = super(CachedResourceMixin, self).dispatch(request, *args, **kwargs)

            # Only cache on OK resposne
            if response.status_code == 200:
                self.cache_response(key, response)

        # Disable client-side caching. Cause IE wrongly assumes that it should
        # cache.
        response['Cache-Control'] = 'no-cache'
        return response

    def get_cache_key(self, request, *args, **kwargs):
        querystring = request.META.get('QUERY_STRING', '')
        contenttype = request.META.get('HTTP_ACCEPT', '')

        # TODO: Eliminate the jQuery cache busting parameter for now. Get
        # rid of this after the old API has been deprecated.
        cache_buster_pattern = re.compile(r'&?_=\d+')
        querystring = re.sub(cache_buster_pattern, '', querystring)

        return ':'.join([self.cache_prefix, contenttype, querystring])

    def respond_from_cache(self, cached_data):
        # Given some cached data, construct a response.
        content, status, headers = cached_data
        response = Response(content, status=status, headers=dict(headers))
        return response

    def cache_response(self, key, response):
        data = response.data
        status = response.status_code
        headers = response.items()

        # Cache enough info to recreate the response.
        django_cache.cache.set(key, (data, status, headers), settings.API_CACHE_TIMEOUT)

        # Also, add the key to the set of pages cached from this view.
        meta_key = self.cache_prefix + '_keys'
        keys = django_cache.cache.get(meta_key) or set()
        keys.add(key)
        django_cache.cache.set(meta_key, keys, settings.API_CACHE_TIMEOUT)

        return response


###############################################################################
#
# Exceptions
# ----------
#

class QueryError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Malformed or missing query parameters.'

    def __init__(self, detail=None):
        self.detail = detail or self.default_detail


###############################################################################
#
# Resource Views
# --------------
#

class PlaceInstanceView (CachedResourceMixin, LocatedResourceMixin, OwnedResourceMixin, FilteredResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET
    ---
    Get a specific place

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_submissions`

        List the submissions in each submission set instead of just a summary of
        the set.

      * `include_invisible` *(only direct auth)*

        Show the place even if it is set as. You must specify use this flag to
        view an invisible place. The flag will also apply to submissions, if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request invisible resoruces.

      * `include_private` *(only direct auth)*

        Show private data attributes on the place, and on any submissions if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request private attributes.

    PUT
    ---
    Update a place

    **Authentication**: Basic, session, or key auth *(required)*

    DELETE
    ------
    Delete a place

    **Authentication**: Basic, session, or key auth *(required)*

    ------------------------------------------------------------
    """

    model = models.Place
    serializer_class = serializers.PlaceSerializer
    renderer_classes = (renderers.GeoJSONRenderer, renderers.GeoJSONPRenderer) + OwnedResourceMixin.renderer_classes[2:]
    parser_classes = (parsers.GeoJSONParser,) + OwnedResourceMixin.parser_classes[1:]

    def get_object_or_404(self, pk):
        try:
            return self.model.objects\
                .filter(pk=pk)\
                .select_related('dataset', 'dataset__owner', 'submitter')\
                .prefetch_related('submitter__social_auth',
                                  'submission_sets__children',
                                  'submission_sets__children__attachments',
                                  'attachments')\
                .get()
        except self.model.DoesNotExist:
            raise Http404

    def get_object(self, queryset=None):
        place_id = self.kwargs['place_id']
        obj = self.get_object_or_404(place_id)
        self.verify_object(obj)
        return obj


class PlaceListView (CachedResourceMixin, LocatedResourceMixin, OwnedResourceMixin, FilteredResourceMixin, generics.ListCreateAPIView):
    """

    GET
    ---
    Get all the places in a dataset

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_submissions`

        List the submissions in each submission set instead of just a summary of
        the set.

      * `include_invisible` *(only direct auth)*

        Show the place even if it is set as. You must specify use this flag to
        view an invisible place. The flag will also apply to submissions, if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request invisible resoruces.

      * `include_private` *(only direct auth)*

        Show private data attributes on the place, and on any submissions if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request private attributes.

      * `near=<reference_geometry>`

        Order the place list by distance from some reference geometry. The
        reference geometry may be represented as
        [GeoJSON](http://www.geojson.org/geojson-spec.html) or
        [WKT](http://en.wikipedia.org/wiki/Well-known_text), or as a
        comma-separated latitude and longitude, if it is a point.

      * `<attr>=<value>`

        Filter the place list to only return the places where the attribute is
        equal to the given value.

    POST
    ----

    Create a place

    **Authentication**: Basic, session, or key auth *(required)*

    ------------------------------------------------------------
    """

    model = models.Place
    serializer_class = serializers.PlaceSerializer
    pagination_serializer_class = serializers.FeatureCollectionSerializer
    renderer_classes = (renderers.GeoJSONRenderer, renderers.GeoJSONPRenderer) + OwnedResourceMixin.renderer_classes[2:]
    parser_classes = (parsers.GeoJSONParser,) + OwnedResourceMixin.parser_classes[1:]

    def pre_save(self, obj):
        super(PlaceListView, self).pre_save(obj)
        obj.dataset = self.get_dataset()

    def get_queryset(self):
        dataset = self.get_dataset()
        queryset = super(PlaceListView, self).get_queryset()

        # If the user is not allowed to request invisible data then we won't
        # be here in the first place.
        if INCLUDE_INVISIBLE_PARAM not in self.request.GET:
            queryset = queryset.filter(visible=True)

        return queryset.filter(dataset=dataset).select_related('dataset', 'dataset__owner', 'submitter')\
            .prefetch_related('submitter__social_auth', 'submission_sets', 'submission_sets__children', 'submission_sets__children__attachments', 'attachments')


class SubmissionInstanceView (CachedResourceMixin, OwnedResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET
    ---
    Get a particular submission

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_invisible` *(only direct auth)*

        Show the submission even if it is set as invisible. You must specify use
        this flag to view an invisible submission. Only the dataset owner is
        allowed to request invisible resoruces.

      * `include_private` *(only direct auth)*

        Show private data attributes on the submission. Only the dataset owner
        is allowed to request private attributes.

    PUT
    ---
    Update a submission

    **Authentication**: Basic, session, or key auth *(required)*

    DELETE
    ------
    Delete a submission

    **Authentication**: Basic, session, or key auth *(required)*

    ------------------------------------------------------------
    """

    model = models.Submission
    serializer_class = serializers.SubmissionSerializer

    def get_object_or_404(self, pk):
        try:
            return self.model.objects\
                .filter(pk=pk)\
                .select_related('dataset', 'dataset_owner', 'parent', 'parent__place', 'submitter')\
                .prefetch_related('attachments', 'submitter__social_auth')\
                .get()
        except self.model.DoesNotExist:
            raise Http404

    def get_object(self, queryset=None):
        submission_id = self.kwargs['submission_id']
        obj = self.get_object_or_404(submission_id)
        self.verify_object(obj)
        return obj


class SubmissionListView (CachedResourceMixin, OwnedResourceMixin, FilteredResourceMixin, generics.ListCreateAPIView):
    """

    GET
    ---
    Get all the submissions in a place's submission set

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_invisible` *(only direct auth)*

        Show the place even if it is set as. You must specify use this flag to
        view an invisible place. The flag will also apply to submissions, if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request invisible resoruces.

      * `include_private` *(only direct auth)*

        Show private data attributes on the place, and on any submissions if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request private attributes.

      * `<attr>=<value>`

        Filter the place list to only return the places where the attribute is
        equal to the given value.

    POST
    ----

    Create a submission

    **Authentication**: Basic, session, or key auth *(required)*

    ------------------------------------------------------------
    """

    model = models.Submission
    serializer_class = serializers.SubmissionSerializer
    pagination_serializer_class = serializers.PaginatedResultsSerializer

    place_id_kwarg = 'place_id'
    submission_set_name_kwarg = 'submission_set_name'

    def get_place(self, dataset):
        place_id = self.kwargs[self.place_id_kwarg]
        place = get_object_or_404(models.Place, dataset=dataset, id=place_id)
        return place

    def get_submission_set(self, dataset, place):
        submission_set_name = self.kwargs[self.submission_set_name_kwarg]

        try:
            submission_set = models.SubmissionSet.objects.get(name=submission_set_name, place=place)
        except models.SubmissionSet.DoesNotExist:
            submission_set = models.SubmissionSet(name=submission_set_name, place=place)

        return submission_set

    def pre_save(self, obj):
        super(SubmissionListView, self).pre_save(obj)
        dataset = self.get_dataset()
        place = self.get_place(dataset)

        # Before we save the submission, we need a submission set as the parent.
        # Check that we have one that exists, and save it if it is not saved.
        parent = self.get_submission_set(dataset, place)
        if parent.pk is None:
            parent.save()
        obj.dataset = dataset
        obj.parent = parent

    def get_queryset(self):
        dataset = self.get_dataset()
        place = self.get_place(dataset)
        submission_set = self.get_submission_set(dataset, place)

        queryset = super(SubmissionListView, self).get_queryset()

        # If the user is not allowed to request invisible data then we won't
        # be here in the first place -- auth or permissions woulda got us.
        if INCLUDE_INVISIBLE_PARAM not in self.request.GET:
            queryset = queryset.filter(visible=True)

        return queryset.filter(parent=submission_set)\
            .select_related('dataset', 'dataset__owner', 'parent', 'parent__place', 'submitter')\
            .prefetch_related('attachments', 'submitter__social_auth')


class DataSetSubmissionListView (CachedResourceMixin, OwnedResourceMixin, FilteredResourceMixin, generics.ListAPIView):
    """

    GET
    ---
    Get all the submissions across a dataset's place's submission sets

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_invisible` *(only direct auth)*

        Show the place even if it is set as. You must specify use this flag to
        view an invisible place. The flag will also apply to submissions, if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request invisible resoruces.

      * `include_private` *(only direct auth)*

        Show private data attributes on the place, and on any submissions if the
        `include_submissions` flag is set. Only the dataset owner is allowed to
        request private attributes.

      * `<attr>=<value>`

        Filter the place list to only return the places where the attribute is
        equal to the given value.

    ------------------------------------------------------------
    """

    model = models.Submission
    serializer_class = serializers.SubmissionSerializer
    pagination_serializer_class = serializers.PaginatedResultsSerializer

    submission_set_name_kwarg = 'submission_set_name'

    def get_submission_sets(self, dataset):
        submission_set_name = self.kwargs[self.submission_set_name_kwarg]
        submission_sets = models.SubmissionSet.objects.filter(name=submission_set_name, place__dataset=dataset)
        return submission_sets

    def get_queryset(self):
        dataset = self.get_dataset()
        submission_sets = self.get_submission_sets(dataset)

        queryset = super(DataSetSubmissionListView, self).get_queryset()

        # If the user is not allowed to request invisible data then we won't
        # be here in the first place -- auth or permissions woulda got us.
        if INCLUDE_INVISIBLE_PARAM not in self.request.GET:
            queryset = queryset.filter(visible=True)

        return queryset.filter(parent__in=submission_sets)\
            .select_related('dataset', 'parent', 'parent__place', 'submitter')\
            .prefetch_related('attachments', 'submitter__social_auth')


class DataSetInstanceView (CachedResourceMixin, OwnedResourceMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET
    ---
    Get a particular submission

    **Authentication**: Basic, session, or key auth *(optional)*

    **Request Parameters**:

      * `include_invisible` *(only direct auth)*

        Count visible and invisible places and submissions in the dataset. Only
        the dataset owner is allowed to request invisible resoruces.

    PUT
    ---
    Update a submission

    **Authentication**: Basic or session auth *(required)*

    DELETE
    ------
    Delete a submission

    **Authentication**: Basic or session auth *(required)*

    ------------------------------------------------------------
    """

    model = models.DataSet
    serializer_class = serializers.DataSetSerializer
    authentication_classes = (authentication.BasicAuthentication, authentication.SessionAuthentication)
    client_authentication_classes = ()

    def get_object_or_404(self, owner_username, dataset_slug):
        try:
            return self.model.objects\
                .filter(slug=dataset_slug, owner__username=owner_username)\
                .select_related('dataset', 'parent__place')\
                .prefetch_related('things__place')\
                .get()
        except self.model.DoesNotExist:
            raise Http404

    @utils.memo
    def get_place_count(self):
        """
        Get the number of places for this dataset.
        """
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET
        places = self.object.places
        if not include_invisible:
            places = places.filter(visible=True)
        return places.count()

    @utils.memo
    def get_submission_sets(self):
        """
        Get a list of submission set summary data for this dataset.
        """
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET
        submissions = self.object.submissions.select_related('parent')
        if not include_invisible:
            submissions = submissions.filter(visible=True)

        # Unset any default ordering
        submissions = submissions.order_by()

        submissions = submissions.values('dataset', 'parent__name').annotate(length=Count('dataset'))
        return submissions

    def get_serializer_context(self):
        context = super(DataSetInstanceView, self).get_serializer_context()
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET

        # The place_count_map_getter returns a dictionary where the keys are
        # dataset ids and the values are corresponding place counts.
        context['place_count_map_getter'] = (
            lambda: {self.object.pk: self.get_place_count()}
        )

        # The submission_sets_map_getter returns a dictionary where the keys are
        # dataset ids and the values are corresponding submission set summaries.
        context['submission_sets_map_getter'] = (
            lambda: {self.object.pk: self.get_submission_sets()}
        )

        return context

    def get_object(self, queryset=None):
        dataset_slug = self.kwargs[self.dataset_slug_kwarg]
        owner_username = self.kwargs[self.owner_username_kwarg]
        obj = self.get_object_or_404(owner_username, dataset_slug)
        self.verify_object(obj)
        return obj

    def put(self, request, owner_username, dataset_slug):
        response = super(DataSetInstanceView, self).put(request, owner_username=owner_username, dataset_slug=dataset_slug)
        if 'slug' in response.data and response.data['slug'] != dataset_slug:
            response.status_code = 301
            response['Location'] = response.data['url']
        return response


class DataSetListMixin (object):
    """
    Common aspects for dataset list views.
    """

    model = models.DataSet
    serializer_class = serializers.DataSetSerializer
    pagination_serializer_class = serializers.PaginatedResultsSerializer
    authentication_classes = (authentication.BasicAuthentication, authentication.SessionAuthentication)
    client_authentication_classes = ()

    @utils.memo
    def get_place_counts(self):
        """
        Return a dictionary whose keys are dataset ids and values are the
        corresponding count of places in that dataset.
        """
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET
        places = models.Place.objects.filter(dataset__in=self.get_queryset())
        if not include_invisible:
            places = places.filter(visible=True)

        # Unset any default ordering
        places = places.order_by()

        places = places.values('dataset').annotate(length=Count('dataset'))
        return dict([(place['dataset'], place['length']) for place in places])

    @utils.memo
    def get_all_submission_sets(self):
        """
        Return a dictionary whose keys are dataset ids and values are a
        corresponding list of submission set summary information for the
        submisisons on that dataset's places.
        """
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET
        summaries = models.Submission.objects.filter(dataset__in=self.get_queryset()).select_related('parent')
        if not include_invisible:
            summaries = summaries.filter(visible=True)

        # Unset any default ordering
        summaries = summaries.order_by()

        summaries = summaries.values('dataset', 'parent__name').annotate(length=Count('dataset'))

        sets = defaultdict(list)
        for summary in summaries:
            sets[summary['dataset']].append(summary)

        return dict(sets.items())

    def get_serializer_context(self):
        context = super(DataSetListMixin, self).get_serializer_context()
        include_invisible = INCLUDE_INVISIBLE_PARAM in self.request.GET

        # The place_count_map_getter returns a dictionary where the keys are
        # dataset ids and the values are corresponding place counts.
        context['place_count_map_getter'] = (
            lambda: self.get_place_counts()
        )

        # The submission_sets_map_getter returns a dictionary where the keys are
        # dataset ids and the values are corresponding submission set summaries.
        context['submission_sets_map_getter'] = (
            lambda: self.get_all_submission_sets()
        )

        return context


class DataSetListView (CachedResourceMixin, DataSetListMixin, OwnedResourceMixin, generics.ListCreateAPIView):
    """

    GET
    ---
    Get all the datasets for a dataset owner

    **Authentication**: Basic, or session auth *(optional)*

    **Request Parameters**:

      * `include_invisible` *(only direct auth)*

        Count visible and invisible places and submissions in the dataset. Only
        the dataset owner is allowed to request invisible resoruces.

    POST
    ----

    Create a dataset

    **Authentication**: Basic or session auth *(required)*

    ------------------------------------------------------------
    """

    def pre_save(self, obj):
        super(DataSetListView, self).pre_save(obj)
        obj.owner = self.get_owner()

    def get_queryset(self):
        owner = self.get_owner()
        queryset = super(DataSetListView, self).get_queryset()
        return queryset.filter(owner=owner).order_by('id')


class AdminDataSetListView (CachedResourceMixin, DataSetListMixin, generics.ListAPIView):
    """

    GET
    ---
    Get all the datasets

    **Authentication**: Basic or session auth *(required)*

    **Request Parameters**:

      * `include_invisible`

        Count visible and invisible places and submissions in the dataset. Only
        the dataset owner is allowed to request invisible resoruces.

    ------------------------------------------------------------
    """

    permission_classes = (IsLoggedInAdmin,)


class AttachmentListView (OwnedResourceMixin, FilteredResourceMixin, generics.ListCreateAPIView):
    """

    GET
    ---
    Get all the attachments for a place or submission

    **Authentication**: Basic, session, or key auth *(optional)*

    POST
    ----

    Attach a file to a place or submission

    **Authentication**: Basic, session, or key auth *(required)*

    ------------------------------------------------------------
    """

    model = models.Attachment
    serializer_class = serializers.AttachmentSerializer

    thing_id_kwarg = 'thing_id'

    def get_thing(self):
        thing_id = self.kwargs[self.thing_id_kwarg]
        dataset = self.get_dataset()
        thing = get_object_or_404(models.SubmittedThing, dataset=dataset, id=thing_id)

        if 'submission_set_name' in self.kwargs:
            obj = thing.submission
            ObjType = models.Submission
        else:
            obj = thing.place
            ObjType = models.Place
        self.verify_object(obj, ObjType)

        return thing

    def get_queryset(self):
        thing = self.get_thing()
        queryset = super(AttachmentListView, self).get_queryset()
        return queryset.filter(thing=thing)

    def pre_save(self, obj):
        super(AttachmentListView, self).pre_save(obj)
        thing = self.get_thing()
        obj.thing = thing


class ActionListView (CachedResourceMixin, OwnedResourceMixin, generics.ListAPIView):
    """

    GET
    ---

    Get the activity for a dataset
    
    **Authentication**: Basic, session, or key auth *(optional)*

    ------------------------------------------------------------
    """
    model = models.Action
    serializer_class = serializers.ActionSerializer
    pagination_serializer_class = serializers.PaginatedResultsSerializer

    def get_queryset(self):
        dataset = self.get_dataset()
        queryset = super(ActionListView, self).get_queryset()\
            .filter(thing__dataset=dataset)\
            .select_related('thing', 'thing__place', 'thing__submission', 'thing__submitter')\
            .prefetch_related('thing__submitter__social_auth')

        if INCLUDE_INVISIBLE_PARAM not in self.request.GET:
            queryset = queryset.filter(thing__visible=True)\
                .filter(Q(thing__place__isnull=False) | 
                        Q(thing__submission__parent__place__visible=True))

        return queryset


class UserInstanceView (OwnedResourceMixin, generics.RetrieveAPIView):
    model = models.User
    serializer_class = serializers.UserSerializer

    def get_queryset(self):
        return models.User.objects.all()\
            .prefetch_related('social_auth')

    def get_object(self, queryset=None):
        owner_username = self.kwargs[self.owner_username_kwarg]
        owner = get_object_or_404(self.get_queryset(), username=owner_username)
        return owner


class CurrentUserInstanceView (CorsEnabledMixin, views.APIView):
    renderer_classes = (JSONRenderer, JSONPRenderer, BrowsableAPIRenderer, renderers.PaginatedCSVRenderer)

    def get(self, request):
        if request.user.is_authenticated():
            user_url = reverse('user-detail', args=[request.user.username])
            return HttpResponseRedirect(user_url + '?' + request.GET.urlencode())
        else:
            return HttpResponse(status=204)


class SessionKeyView (CorsEnabledMixin, views.APIView):
    renderer_classes = (JSONRenderer, JSONPRenderer, BrowsableAPIRenderer)

    def get(self, request):
        return Response({
            settings.SESSION_COOKIE_NAME: request.session.session_key,
        })


###############################################################################
#
# Social Authentication Views
# ---------------------------
#

def build_relative_url(original_url, relative_path):
    """
    Given a source URL, create a full URL for the relative path. For example:

    ('http://ex.co/pictures/silly/abc.png', '/home') --> 'http://ex.co/home'
    ('http://ex.co/p/index.html', 'about.html') --> 'http://ex.co/p/about.html'
    """
    parsed_url = urlparse(original_url)

    if relative_path.startswith('/'):
        path_prefix = ''
    elif parsed_url.path.endswith('/') or relative_path == '':
        path_prefix = parsed_url.path
    else:
        path_prefix = parsed_url.path.rsplit('/', 1)[:-1] + '/'

    if path_prefix:
        full_path = path_prefix + relative_path
    else:
        full_path = relative_path

    return urljoin(parsed_url.scheme + '://' + parsed_url.netloc, full_path)

def capture_referer(view_func):
    """
    A wrapper for views that redirect with a 'next' parameter to any
    arbitrary URL. Normally, Django (and social-auth) internals only allow
    redirecting to paths on the current host.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        client_next = request.GET.get('next', '')
        referer = request.META.get('HTTP_REFERER')

        if referer:
            client_next = build_relative_url(referer, client_next)
        else:
            return HttpResponseBadRequest('Referer header must be set.')

        request.GET = request.GET.copy()
        request.GET['next'] = reverse('redirector') + '?' + urlencode({'target': client_next})

        return view_func(request, *args, **kwargs)

    return wrapper

remote_social_login = capture_referer(social_views.auth)
remote_logout = capture_referer(auth_views.logout)

# social_auth_login = use_social_auth_headers(social_views.auth)
# social_auth_complete = use_social_auth_headers(social_views.complete)

def redirector(request):
    """
    Simple view to redirect to external URL.
    """
    try:
        target = request.GET['target']
    except KeyError:
        return HttpResponseBadRequest('No target specified to redirect to.')

    return HttpResponseRedirect(target)

#from . import forms
#from . import models
#from . import parsers
#from . import renderers
#from . import resources
#from . import utils
#from django.conf import settings
#from django.contrib import auth
#from django.contrib.gis import geos
#from django.core.cache import cache
#from django.http import HttpResponse
#from django.shortcuts import get_object_or_404
#from django.views.decorators.csrf import csrf_exempt
#from djangorestframework import views, permissions, mixins, authentication, status
#from djangorestframework.response import Response, ErrorResponse
#import apikey.auth
#import ujson as json
#import logging
#import os
#import re
#import time

#logger = logging.getLogger('sa_api_v2.views')


#def raise_error_if_not_authenticated(view, request):
#    # TODO: delete this
#    if getattr(request, 'user', None) is None:
#        # Probably happens only in tests that have forgotten to set the user.
#        raise permissions._403_FORBIDDEN_RESPONSE
#    if isinstance(view, mixins.AuthMixin):
#        # This triggers authentication (view.user is a property).
#        user = view.user
#    else:
#        user = request.user
#    permissions.IsAuthenticated(view).check_permission(user)


#class IsOwnerOrSuperuser(permissions.BasePermission):
#    def check_permission(self, user):
#        """
#        Allows only superusers or the user named by
#        ``self.view.allowed_username``.

#        (If the view has no such attribute, raises a 403 Forbidden
#        exception.  Subclasses of AuthMixin should have it.)
#        """
#        if user.is_superuser:
#            # XXX Watch out when mocking users in tests: bool(mock.Mock()) is True
#            return
#        username = getattr(user, 'username', None)
#        if username and (self.view.allowed_username == username):
#            return
#        raise permissions._403_FORBIDDEN_RESPONSE


#class IsOwnerOrSuperuserWithoutApiKey(IsOwnerOrSuperuser):
#    def check_permission(self, user):
#        """Like IsOwnerOrSuperuser, but will not respond to any
#        request with the API key http header.

#        For protecting views related to API keys that should require
#        'real' authentication, to avoid users abusing one API key to
#        obtain others.
#        """
#        from .apikey.auth import KEY_HEADER
#        if KEY_HEADER in self.view.request.META:
#            raise permissions._403_FORBIDDEN_RESPONSE

#        return super(IsOwnerOrSuperuserWithoutApiKey, self).check_permission(user)


#class CanShowPrivateData (permissions.BasePermission):
#    def check_permission(self, user):
#        if not self.view.flags.get('include_private_data'):
#            return

#        if hasattr(user, 'is_directly_authenticated') and user.is_directly_authenticated is True:
#            if user.is_superuser or user.username == self.view.allowed_username:
#                return

#        raise permissions._403_FORBIDDEN_RESPONSE


#class DirectAuthenticationMixin (object):
#    def authenticate(self, request):
#        user = super(DirectAuthenticationMixin, self).authenticate(request)

#        # If it passed the parent's authentication, specify that the user is
#        # authenticated directly, and not through an API key.
#        if user is not None:
#            user.is_directly_authenticated = True

#        return user


#class BasicAuthentication (DirectAuthenticationMixin, authentication.BasicAuthentication):
#    pass


#class UserLoggedInAuthentication (DirectAuthenticationMixin, authentication.UserLoggedInAuthentication):
#    pass


#class AuthMixin(object):
#    """
#    Inherit from this to protect all unsafe requests
#    with permissions listed in ``self.unsafe_permissions``.

#    You should set the ``owner_username_kwarg`` attribute to tell dispatch()
#    how to get the name of the resource's owner from the request kwargs;
#    """
#    authentication = [BasicAuthentication,
#                      UserLoggedInAuthentication,
#                      apikey.auth.ApiKeyAuthentication]

#    unsafe_permissions = [IsOwnerOrSuperuser]

#    allowed_username = None
#    owner_username_kwarg = None

#    def dispatch(self, request, *args, **kwargs):
#        # We do this in dispatch() so we can apply permission checks
#        # to only some request methods.
#        self.request = request  # Not sure what needs this.

#        # This triggers authentication (view.user is a property).
#        user = self.user

#        if self.owner_username_kwarg:
#            self.allowed_username = kwargs[self.owner_username_kwarg]
#        elif self.allowed_username:
#            pass
#        else:
#            logger.error("Subclass %s of AuthMixin is supposed to provide .owner_username_kwarg or .allowed_username" % self)
#            return permissions._403_FORBIDDEN_RESPONSE.response

#        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
#            try:
#                for perm in getattr(self, 'unsafe_permissions', []):
#                    perm(self).check_permission(user)
#            except ErrorResponse as e:
#                content = json.dumps(e.response.raw_content)
#                response = HttpResponse(content, status=e.response.status)
#                response['Content-Type'] = 'application/json'
#                return response
#        return super(AuthMixin, self).dispatch(request, *args, **kwargs)


#class CachedMixin (object):
#    @property
#    def cache_prefix(self):
#        return self.request.path

#    def get_cache_prefix(self):
#        return self.cache_prefix

#    def get_cache_metakey(self):
#        prefix = self.cache_prefix
#        return prefix + '_keys'

#    @csrf_exempt
#    def dispatch(self, request, *args, **kwargs):
#        # Only do the cache for GET method.
#        if request.method.lower() != 'get':
#            return super(CachedMixin, self).dispatch(request, *args, **kwargs)

#        # Check whether the response data is in the cache.
#        key = self.get_cache_key(request, *args, **kwargs)
#        response_data = cache.get(key) or None

#        # Also check whether the request cache key is managed in the cache.
#        # This is important, because if it's not managed, then we'll never
#        # know when to invalidate it. If it's not managed we should just
#        # assume that it's invalid.
#        metakey = self.get_cache_metakey()
#        keyset = cache.get(metakey) or set()

#        if (response_data is not None) and (key in keyset):
#            cached_response = self.respond_from_cache(response_data)

#            # Patch the HTTP method
#            setattr(self, self.method.lower(),
#                    lambda *args, **kwargs: cached_response)

#            response = super(CachedMixin, self).dispatch(request, *args, **kwargs)
#        else:
#            response = super(CachedMixin, self).dispatch(request, *args, **kwargs)

#            # Only cache on OK resposne
#            if response.status_code == 200:
#                self.cache_response(key, response)

#        # Disable client-side caching. Cause IE wrongly assumes that it should
#        # cache.
#        response['Cache-Control'] = 'no-cache'
#        return response

#    def get_cache_key(self, request, *args, **kwargs):
#        querystring = request.META['QUERY_STRING']
#        contenttype = request.META['HTTP_ACCEPT']

#        # TODO: Eliminate the jQuery cache busting parameter for now. Get
#        # rid of this after the old API has been deprecated.
#        cache_buster_pattern = re.compile(r'&?_=\d+')
#        querystring = re.sub(cache_buster_pattern, '', querystring)

#        return ':'.join([self.cache_prefix, contenttype, querystring])

#    def respond_from_cache(self, cached_data):
#        # Given some cached data, construct a response.
#        content, status, headers = cached_data
#        response = HttpResponse(content,
#                                status=status)
#        for key, value in headers:
#            response[key] = value

#        return response

#    def cache_response(self, key, response):
#        content = response.content
#        status = response.status_code
#        headers = response.items()

#        # Cache enough info to recreate the response.
#        cache.set(key, (content, status, headers), settings.API_CACHE_TIMEOUT)

#        # Also, add the key to the set of pages cached from this view.
#        meta_key = self.cache_prefix + '_keys'
#        keys = cache.get(meta_key) or set()
#        keys.add(key)
#        cache.set(meta_key, keys, settings.API_CACHE_TIMEOUT)


#class AbsUrlMixin (object):
#    def filter_response(self, obj):
#        """
#        Given the response content, filter it into a serializable object.
#        """
#        filtered = super(AbsUrlMixin, self).filter_response(obj)
#        return self.process_urls(filtered)

#    def process_urls(self, data):
#        """
#        Recursively replace all 'url' attributes with absolute URIs.  Operation
#        is done in place.
#        """
#        if isinstance(data, list):
#            for val in data:
#                self.process_urls(val)

#        elif isinstance(data, dict):
#            if data.get('url') is not None:
#                data['url'] = self.request.build_absolute_uri(data['url'])

#            for val in data.itervalues():
#                self.process_urls(val)

#        return data


#class Ignore_CacheBusterMixin (object):
#    @csrf_exempt
#    def dispatch(self, request, *args, **kwargs):
#        # In order to ensure the return of a non-cached version of the view,
#        # jQuery adds an _ query parameter with random data.  Ignore that
#        # parameter so that it doesn't get passed along to our form validation.
#        get_params = request.GET.copy()
#        if '_' in get_params:
#            get_params.pop('_')
#        request.GET = get_params

#        return super(Ignore_CacheBusterMixin, self).dispatch(request, *args, **kwargs)


#class ActivityGeneratingMixin (object):
#    def get_save_kwargs(self):
#        silent_header = self.request.META.get('HTTP_X_SHAREABOUTS_SILENT', 'False')
#        silent = silent_header.lower() in ('true', 't', 'yes', 'y')
#        return {'silent': silent}


#class OwnerAwareMixin (object):
#    def dispatch(self, request, *args, **kwargs):
#        # owner_username_kwarg should be the name of the kwarg in the URL that
#        # designates the username of the owner of the resource
#        self.allowed_username = kwargs[self.owner_username_kwarg]
#        return super(OwnerAwareMixin, self).dispatch(request, *args, **kwargs)


#class ModelViewWithDataBlobMixin (OwnerAwareMixin):
#    """
#    Views on things that store flexible data derive from this.

#    Attributes:
#    -----------
#    flags - A dictionary of values that modify what data to show or hide.
#    * 'include_invisible': Show visible and invisible objects
#    * 'include_private_data': Show data attributes that begin with a private
#                              prefix
#    * 'include_submissions': Show the full submission data attached to objects,
#                             not just the summary counts
#    """

#    parsers = parsers.DEFAULT_DATA_BLOB_PARSERS

#    default_modifiers = {
#        'include_invisible': False,
#        'include_private_data': False,
#        'include_submissions': False,
#    }

#    def __init__(self, *args, **kwargs):
#        self.flags = self.default_modifiers.copy()
#        self.permissions += (CanShowPrivateData,)
#        super(ModelViewWithDataBlobMixin, self).__init__(*args, **kwargs)

#    def calculate_flags(self, request):
#        if request.GET.get('include_submissions', 'false').lower() != 'false':
#            self.flags['include_submissions'] = True
#        if request.GET.get('include_invisible', 'false').lower() != 'false':
#            self.flags['include_invisible'] = True
#        if request.GET.get('include_private_data', 'false').lower() != 'false':
#            self.flags['include_private_data'] = True

#    def initial(self, request, *args, **kwargs):
#        self.calculate_flags(request)
#        return super(ModelViewWithDataBlobMixin, self).initial(request, *args, **kwargs)

#    def _perform_form_overloading(self):
#        """
#        Overloaded to handle the data blob as submitted from a form.
#        """
#        super(ModelViewWithDataBlobMixin, self)._perform_form_overloading()
#        if hasattr(self, '_data'):
#            utils.unpack_data_blob(self._data)


#class OwnerCollectionView (AbsUrlMixin, views.ListModelView):
#    resource = resources.OwnerResource


#class DataSetCollectionView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ModelViewWithDataBlobMixin, CachedMixin, views.ListOrCreateModelView):
#    resource = resources.DataSetResource

#    owner_username_kwarg = 'owner__username'

#    def get_instance_data(self, model, content, **kwargs):
#        # Used by djangorestframework to make args to build an instance for POST
#        kwargs.pop('owner__username', None)
#        content['owner'] = get_object_or_404(auth.models.User, username=self.allowed_username)
#        return super(DataSetCollectionView, self).get_instance_data(model, content, **kwargs)

#    def post(self, request, *args, **kwargs):
#        response = super(DataSetCollectionView, self).post(request, *args, **kwargs)
#        # Create an API key for the DataSet we just created.
#        dataset = response.raw_content
#        from .apikey.models import ApiKey, generate_unique_api_key
#        key = ApiKey()
#        key.user_id = dataset.owner.id  # TODO: do not allow anonymous
#        key.key = generate_unique_api_key()
#        key.save()
#        dataset.api_keys.add(key)
#        return response


#class DataSetInstanceView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ModelViewWithDataBlobMixin, CachedMixin, views.InstanceModelView):
#    resource = resources.DataSetResource

#    owner_username_kwarg = 'owner__username'

#    def put(self, request, *args, **kwargs):
#        instance = super(DataSetInstanceView, self).put(request, *args, **kwargs)
#        renamed = ('slug' in kwargs and
#                   (kwargs['slug'] != instance.slug))
#        headers = {}
#        if renamed:
#            headers['Location'] = self.resource(self).url(instance)
#            # http://en.wikipedia.org/wiki/HTTP_303
#            response = Response(303, instance, headers)
#            return response
#        else:
#            # djangorestframework will wrap it in a 200 response.
#            return instance


#class PlaceCollectionView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ActivityGeneratingMixin, CachedMixin, ModelViewWithDataBlobMixin, views.ListOrCreateModelView):
#    # TODO: Decide whether pagination is appropriate/necessary.
#    resource = resources.PlaceResource

#    owner_username_kwarg = 'dataset__owner__username'

#    def get_instance_data(self, model, content, **kwargs):
#        # Used by djangorestframework to make args to build an instance for POST
#        dataset = get_object_or_404(
#            models.DataSet,
#            slug=kwargs.pop('dataset__slug'),
#            owner__username=kwargs.pop('dataset__owner__username'),
#        )
#        content['dataset'] = dataset
#        return super(PlaceCollectionView, self).get_instance_data(model, content, **kwargs)

#    def get_queryset(self):
#        queryset = super(PlaceCollectionView, self).get_queryset()

#        if not self.flags['include_invisible']:
#            queryset = queryset.filter(visible=True)

#        if 'near' in self.request.GET:
#            try:
#                lat, lng = map(float, self.request.GET['near'].split(','))
#            except ValueError:
#                raise ErrorResponse(
#                    status.HTTP_400_BAD_REQUEST,
#                    {'detail': 'The near parameter should be a comma-separated pair of numbers.'})

#            queryset = queryset.distance(geos.Point(lng, lat)).order_by('distance')

#        return queryset

#    def get(self, request, *args, **kwargs):
#        request.GET = request.GET.copy()
#        skip = request.GET.pop('skip', [None])[0]
#        limit = request.GET.pop('limit', [None])[0]

#        start, end = skip, (int(limit)+int(skip) if limit and skip else limit)

#        queryset = super(PlaceCollectionView, self).get(request, *args, **kwargs)
#        return queryset[start:end]

#    def post(self, request, *args, **kwargs):
#        response = super(PlaceCollectionView, self).post(request, *args, **kwargs)
#        # djangorestframework automagically sets Location, but ...
#        # see comment on DataSetCollectionView.post()
#        response.headers['Location'] = self._resource.url(response.raw_content)
#        return response


#class PlaceInstanceView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ActivityGeneratingMixin, ModelViewWithDataBlobMixin, CachedMixin, views.InstanceModelView):

#    owner_username_kwarg = 'dataset__owner__username'

#    resource = resources.PlaceResource


#class ApiKeyCollectionView (Ignore_CacheBusterMixin, AbsUrlMixin, OwnerAwareMixin, views.ListModelView):
#    """
#    Get a list of API keys valid for this DataSet.

#    This resource cannot itself be accessed using an API key, as that
#    could allow a client to use one key to obtain all the other keys.

#    Accordingly, we require HTTP basic auth for all requests to this
#    resource, and you have to be the DataSet owner or a superuser.

#    The resource should only be exposed via https.
#    """

#    resource = resources.ApiKeyResource
#    permissions = (permissions.IsAuthenticated, IsOwnerOrSuperuserWithoutApiKey)
#    # We do NOT allow key-based auth here, as that would allow
#    # using one key to obtain other keys.
#    # Only the owner of a dataset can use this child resource.
#    authentication = (authentication.BasicAuthentication,
#                      authentication.UserLoggedInAuthentication)

#    owner_username_kwarg = 'datasets__owner__username'

#    def dispatch(self, request, *args, **kwargs):
#        # Set up context needed by permissions checks.
#        self.dataset = get_object_or_404(
#            models.DataSet,
#            owner__username=kwargs[self.owner_username_kwarg],
#            slug=kwargs['datasets__slug'])
#        self.request = request  # Not sure what needs this.
#        return super(ApiKeyCollectionView, self).dispatch(request, *args, **kwargs)

#    # TODO: handle POST, DELETE


#class AllSubmissionCollectionsView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ActivityGeneratingMixin, ModelViewWithDataBlobMixin, CachedMixin, views.ListModelView):
#    resource = resources.SubmissionResource

#    owner_username_kwarg = 'dataset__owner__username'

#    def get(self, request, submission_type, **kwargs):
#        # If the submission_type is specific, then filter by that type.
#        if submission_type != 'submissions':
#            kwargs['parent__submission_type'] = submission_type

#        return super(AllSubmissionCollectionsView, self).get(
#            request,
#            **kwargs
#        )


#class SubmissionCollectionView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ActivityGeneratingMixin, ModelViewWithDataBlobMixin, CachedMixin, views.ListOrCreateModelView):
#    resource = resources.SubmissionResource

#    owner_username_kwarg = 'dataset__owner__username'

#    def get(self, request, place_id, submission_type, **kwargs):
#        # rename the URL parameters as necessary, and pass to the
#        # base class's handler
#        return super(SubmissionCollectionView, self).get(
#            request,
#            parent__place_id=place_id,
#            parent__submission_type=submission_type,
#            **kwargs
#        )

#    def get_queryset(self):
#        queryset = super(SubmissionCollectionView, self).get_queryset()

#        show_invisible = self.request.GET.get('include_invisible', 'false')
#        if (show_invisible.lower() == 'false'):
#            return queryset.filter(visible=True)
#        else:
#            return queryset

#    def post(self, request, place_id, submission_type, **kwargs):
#        # TODO: Location
#        return super(SubmissionCollectionView, self).post(
#            request, place_id=place_id, submission_type=submission_type, **kwargs)

#    def get_instance_data(self, model, content, **kwargs):
#        # Used by djangorestframework to make args to build an instance for POST
#        # From the URL string, we should have the necessary
#        # information to get the submission set.  The DataSet is
#        # implicit from the Place, which we get by ID, so ignore the
#        # extra kwargs.
#        place_id = kwargs['place_id']
#        submission_type = kwargs['submission_type']
#        place = get_object_or_404(models.Place, id=place_id)
#        submission_set, created = models.SubmissionSet.objects.get_or_create(
#            place_id=place_id, submission_type=submission_type)

#        # TODO If there's a validation error with the submission, we may end up
#        #      with a dangling submission_set.  We should either defer the
#        #      creation of the set, or make sure it gets cleaned up on error.

#        content['dataset'] = place.dataset
#        content['parent'] = submission_set
#        # We don't pass the remaining kwargs as we already have the
#        # DatSet they indirectly identify, and Submission can't
#        # directly handle them anyway.
#        return super(SubmissionCollectionView, self).get_instance_data(model, content,)


#class SubmissionInstanceView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, ActivityGeneratingMixin, ModelViewWithDataBlobMixin, CachedMixin, views.InstanceModelView):
#    resource = resources.SubmissionResource

#    owner_username_kwarg = 'dataset__owner__username'

#    def get_instance(self, **kwargs):
#        """
#        Get a model instance for read/update/delete requests.
#        """
#        # This could do more joins using the kwargs if necessary,
#        # but as long as we have pk in the URL, that's a fast query...
#        return super(SubmissionInstanceView, self).get_instance(pk=kwargs['pk'])


## TODO derive from CachedMixin to enable caching
#class ActivityView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, CachedMixin, views.ListModelView):
#    """
#    Get a list of activities ordered by the `created_datetime` in reverse.

#    Query String Parameters
#    -----------------------
#    - `before` -- The id of the latest activity to return.  The
#                  most recent results with the given id or lower will be
#                  returned.
#    - `after` -- The id of the earliest activity to return.  The
#                 most recent results with ids higher than *but not including*
#                 the given time will be returned.
#    - `limit` -- The maximum number of results to be returned.
#    - `visible` -- Set to `all` to return activity for both visible and
#                   invisible places.

#    Examples
#    --------
#    Get up to the 50 most recent activities:

#        /activity/?limit=50

#    When polling for all new updates, use the id of the last known
#    activity with the `after` parameter:

#        /activity/?after=<last_known_id>
#    """
#    resource = resources.ActivityResource
#    form = forms.ActivityForm

#    owner_username_kwarg = 'data__dataset__owner__username'

#    def get_places(self):
#        visibility = self.PARAMS.get('visible', 'true')
#        if (visibility == 'all'):
#            return models.Place.objects.all()
#        elif visibility == 'true' or visibility == '':
#            return models.Place.objects.all().filter(visible=True)
#        else:
#            raise Exception('Invalid visibility: ' + repr(visibility))

#    def get_submissions(self):
#        visibility = self.PARAMS.get('visible', 'true')
#        if (visibility == 'all'):
#            return models.Submission.objects.all().select_related('parent')
#        elif visibility == 'true' or visibility == '':
#            return models.Submission.objects.all().filter(visible=True).select_related('parent').filter(parent__place__visible=True)
#        else:
#            raise Exception('Invalid visibility: ' + repr(visibility))

#    def get_queryset(self):
#        """
#        Get a list containing objects of all the activity matching the given
#        query parameters.

#        We don't do 'limit' here because subclasses may want to do
#        additional filtering; do it in get() instead. (Also easier to test.)
#        """
#        # Validate the query and get the parameters
#        activity = self._resource.queryset
#        query_params = self.PARAMS
#        latest_id = query_params.get('before')
#        earliest_id = query_params.get('after')

#        activity = activity.order_by('-id')

#        if earliest_id:
#            activity = activity.filter(id__gt=earliest_id)

#        if latest_id:
#            activity = activity.filter(id__lte=latest_id)

#        return activity

#    def get(self, request, *args, **kwargs):
#        """
#        Optionally limit number of items per the 'limit' query param.
#        """
#        queryset = super(ActivityView, self).get(request, *args, **kwargs)
#        limit = self.PARAMS.get('limit')
#        if limit is not None:
#            queryset = queryset[:limit]
#        return queryset


#class OwnerPasswordView (Ignore_CacheBusterMixin, AuthMixin, AbsUrlMixin, views.View):
#    owner_username_kwarg = 'owner__username'
#    parsers = [parsers.PlainTextParser]

#    def put(self, request, owner__username):
#        new_password = self.DATA
#        owner = auth.models.User.objects.get(username=owner__username)
#        owner.set_password(new_password)
#        owner.save()
#        return Response(204)


#class TabularPlaceCollectionView (PlaceCollectionView):
#    resource = resources.TabularPlaceResource


#class TabularSubmissionCollectionView (SubmissionCollectionView):
#    resource = resources.TabularSubmissionResource


#class TabularAllSubmissionCollectionsView (AllSubmissionCollectionsView):
#    resource = resources.TabularSubmissionResource


#class AttachmentView (Ignore_CacheBusterMixin, AuthMixin, views.ListOrCreateModelView):
#    resource = resources.AttachmentResource
#    owner_username_kwarg = 'dataset__owner__username'

#    def post(self, request, **kwargs):
#        return super(AttachmentView, self).post(request, thing_id=kwargs['thing_id'])

#    def get(self, request, **kwargs):
#        return super(AttachmentView, self).get(request, thing_id=kwargs['thing_id'])
