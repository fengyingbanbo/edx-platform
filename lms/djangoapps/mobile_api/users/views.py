# -*- coding:utf-8 -*-
"""
Views for user API
"""

import json
import logging

from edx_ace import ace
from edx_ace.recipient import Recipient

from six import iteritems, text_type
from django.shortcuts import redirect
from django.utils import dateparse
from django.contrib.sites.models import Site
from django.contrib.auth import authenticate, get_user_model, logout
from django.db import transaction
from django.utils.translation import ugettext as _

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import generics, views
from rest_framework.decorators import api_view
from rest_framework.response import Response
from xblock.fields import Scope
from xblock.runtime import KeyValueStore

from courseware.access import is_mobile_available_for_user
from courseware.courses import get_current_child
from courseware.model_data import FieldDataCache
from courseware.module_render import get_module_for_descriptor
from courseware.views.index import save_positions_recursively_up
from experiments.models import ExperimentData, ExperimentKeyValue
from lms.djangoapps.courseware.access_utils import ACCESS_GRANTED
from mobile_api.utils import API_V05
from openedx.features.course_duration_limits.access import check_course_expired
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    RetirementStateError,
    UserOrgTag,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus
)
from openedx.core.djangoapps.user_api.accounts.views import _set_unusable_password
from openedx.core.djangoapps.user_api.message_types import DeletionNotificationMessage
from openedx.core.djangolib.oauth2_retirement_utils import retire_dot_oauth2_models, retire_dop_oauth2_models
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.user_authn.exceptions import AuthFailedError

from student.models import CourseEnrollment, User, get_retired_email_by_email, Registration, LoginFailures
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from rest_framework import permissions, status

from .. import errors
from ..decorators import mobile_course_access, mobile_view
from .serializers import CourseEnrollmentSerializer, CourseEnrollmentSerializerv05, UserSerializer

from social_django.models import UserSocialAuth


log = logging.getLogger(__name__)


@mobile_view(is_user=True)
class UserDetail(generics.RetrieveAPIView):
    """
    **Use Case**

        Get information about the specified user and access other resources
        the user has permissions for.

        Users are redirected to this endpoint after they sign in.

        You can use the **course_enrollments** value in the response to get a
        list of courses the user is enrolled in.

    **Example Request**

        GET /api/mobile/{version}/users/{username}

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * course_enrollments: The URI to list the courses the currently signed
          in user is enrolled in.
        * email: The email address of the currently signed in user.
        * id: The ID of the user.
        * name: The full name of the currently signed in user.
        * username: The username of the currently signed in user.
    """
    queryset = (
        User.objects.all().select_related('profile')
    )
    serializer_class = UserSerializer
    lookup_field = 'username'

    def get_serializer_context(self):
        context = super(UserDetail, self).get_serializer_context()
        context['api_version'] = self.kwargs.get('api_version')
        return context


@mobile_view(is_user=True)
class UserCourseStatus(views.APIView):
    """
    **Use Cases**

        Get or update the ID of the module that the specified user last
        visited in the specified course.

    **Example Requests**

        GET /api/mobile/{version}/users/{username}/course_status_info/{course_id}

        PATCH /api/mobile/{version}/users/{username}/course_status_info/{course_id}

        **PATCH Parameters**

          The body of the PATCH request can include the following parameters.

          * last_visited_module_id={module_id}
          * modification_date={date}

            The modification_date parameter is optional. If it is present, the
            update will only take effect if the modification_date in the
            request is later than the modification_date saved on the server.

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * last_visited_module_id: The ID of the last module that the user
          visited in the course.
        * last_visited_module_path: The ID of the modules in the path from the
          last visited module to the course module.
    """

    http_method_names = ["get", "patch"]

    def _last_visited_module_path(self, request, course):
        """
        Returns the path from the last module visited by the current user in the given course up to
        the course module. If there is no such visit, the first item deep enough down the course
        tree is used.
        """
        field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
            course.id, request.user, course, depth=2)

        course_module = get_module_for_descriptor(
            request.user, request, course, field_data_cache, course.id, course=course
        )

        path = [course_module]
        chapter = get_current_child(course_module, min_depth=2)
        if chapter is not None:
            path.append(chapter)
            section = get_current_child(chapter, min_depth=1)
            if section is not None:
                path.append(section)

        path.reverse()
        return path

    def _get_course_info(self, request, course):
        """
        Returns the course status
        """
        path = self._last_visited_module_path(request, course)
        path_ids = [unicode(module.location) for module in path]
        return Response({
            "last_visited_module_id": path_ids[0],
            "last_visited_module_path": path_ids,
        })

    def _update_last_visited_module_id(self, request, course, module_key, modification_date):
        """
        Saves the module id if the found modification_date is less recent than the passed modification date
        """
        field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
            course.id, request.user, course, depth=2)
        try:
            module_descriptor = modulestore().get_item(module_key)
        except ItemNotFoundError:
            return Response(errors.ERROR_INVALID_MODULE_ID, status=400)
        module = get_module_for_descriptor(
            request.user, request, module_descriptor, field_data_cache, course.id, course=course
        )

        if modification_date:
            key = KeyValueStore.Key(
                scope=Scope.user_state,
                user_id=request.user.id,
                block_scope_id=course.location,
                field_name='position'
            )
            original_store_date = field_data_cache.last_modified(key)
            if original_store_date is not None and modification_date < original_store_date:
                # old modification date so skip update
                return self._get_course_info(request, course)

        save_positions_recursively_up(request.user, request, field_data_cache, module, course=course)
        return self._get_course_info(request, course)

    @mobile_course_access(depth=2)
    def get(self, request, course, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Get the ID of the module that the specified user last visited in the specified course.
        """

        return self._get_course_info(request, course)

    @mobile_course_access(depth=2)
    def patch(self, request, course, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Update the ID of the module that the specified user last visited in the specified course.
        """
        module_id = request.data.get("last_visited_module_id")
        modification_date_string = request.data.get("modification_date")
        modification_date = None
        if modification_date_string:
            modification_date = dateparse.parse_datetime(modification_date_string)
            if not modification_date or not modification_date.tzinfo:
                return Response(errors.ERROR_INVALID_MODIFICATION_DATE, status=400)

        if module_id:
            try:
                module_key = UsageKey.from_string(module_id)
            except InvalidKeyError:
                return Response(errors.ERROR_INVALID_MODULE_ID, status=400)

            return self._update_last_visited_module_id(request, course, module_key, modification_date)
        else:
            # The arguments are optional, so if there's no argument just succeed
            return self._get_course_info(request, course)


@mobile_view(is_user=True)
class UserCourseEnrollmentsList(generics.ListAPIView):
    """
    **Use Case**

        Get information about the courses that the currently signed in user is
        enrolled in.

        v1 differs from v0.5 version by returning ALL enrollments for
        a user rather than only the enrollments the user has access to (that haven't expired).
        An additional attribute "expiration" has been added to the response, which lists the date
        when access to the course will expire or null if it doesn't expire.

    **Example Request**

        GET /api/mobile/v1/users/{username}/course_enrollments/

    **Response Values**

        If the request for information about the user is successful, the
        request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * expiration: The course expiration date for given user course pair
          or null if the course does not expire.
        * certificate: Information about the user's earned certificate in the
          course.
        * course: A collection of the following data about the course.

        * courseware_access: A JSON representation with access information for the course,
          including any access errors.

          * course_about: The URL to the course about page.
          * course_sharing_utm_parameters: Encoded UTM parameters to be included in course sharing url
          * course_handouts: The URI to get data for course handouts.
          * course_image: The path to the course image.
          * course_updates: The URI to get data for course updates.
          * discussion_url: The URI to access data for course discussions if
            it is enabled, otherwise null.
          * end: The end date of the course.
          * id: The unique ID of the course.
          * name: The name of the course.
          * number: The course number.
          * org: The organization that created the course.
          * start: The date and time when the course starts.
          * start_display:
            If start_type is a string, then the advertised_start date for the course.
            If start_type is a timestamp, then a formatted date for the start of the course.
            If start_type is empty, then the value is None and it indicates that the course has not yet started.
          * start_type: One of either "string", "timestamp", or "empty"
          * subscription_id: A unique "clean" (alphanumeric with '_') ID of
            the course.
          * video_outline: The URI to get the list of all videos that the user
            can access in the course.

        * created: The date the course was created.
        * is_active: Whether the course is currently active. Possible values
          are true or false.
        * mode: The type of certificate registration for this course (honor or
          certified).
        * url: URL to the downloadable version of the certificate, if exists.
    """
    queryset = CourseEnrollment.objects.all()
    lookup_field = 'username'

    # In Django Rest Framework v3, there is a default pagination
    # class that transmutes the response data into a dictionary
    # with pagination information.  The original response data (a list)
    # is stored in a "results" value of the dictionary.
    # For backwards compatibility with the existing API, we disable
    # the default behavior by setting the pagination_class to None.
    pagination_class = None

    def is_org(self, check_org, course_org):
        """
        Check course org matches request org param or no param provided
        """
        return check_org is None or (check_org.lower() == course_org.lower())

    def hide_course_for_enrollment_fee_experiment(self, user, enrollment, experiment_id=9):
        """
        Hide enrolled courses from mobile app as part of REV-73/REV-19
        """
        course_key = enrollment.course_overview.id
        try:
            courses_excluded_from_mobile = ExperimentKeyValue.objects.get(
                experiment_id=10,
                key="mobile_app_exclusion"
            ).value
            courses_excluded_from_mobile = json.loads(courses_excluded_from_mobile.replace('\r', '').replace('\n', ''))
            if enrollment.mode == 'audit' and str(course_key) in courses_excluded_from_mobile.keys():
                activationTime = dateparse.parse_datetime(courses_excluded_from_mobile[str(course_key)])
                if activationTime and enrollment.created and enrollment.created > activationTime:
                    return True
        except (ExperimentKeyValue.DoesNotExist, AttributeError):
            pass

        try:
            ExperimentData.objects.get(
                user=user,
                experiment_id=experiment_id,
                key='enrolled_{0}'.format(course_key),
            )
        except ExperimentData.DoesNotExist:
            return False

        try:
            ExperimentData.objects.get(
                user=user,
                experiment_id=experiment_id,
                key='paid_{0}'.format(course_key),
            )
        except ExperimentData.DoesNotExist:
            return True

        return False

    def get_serializer_context(self):
        context = super(UserCourseEnrollmentsList, self).get_serializer_context()
        context['api_version'] = self.kwargs.get('api_version')
        return context

    def get_serializer_class(self):
        api_version = self.kwargs.get('api_version')
        if api_version == API_V05:
            return CourseEnrollmentSerializerv05
        return CourseEnrollmentSerializer

    def get_queryset(self):
        api_version = self.kwargs.get('api_version')
        enrollments = self.queryset.filter(
            user__username=self.kwargs['username'],
            is_active=True
        ).order_by('created').reverse()
        org = self.request.query_params.get('org', None)

        same_org = (
            enrollment for enrollment in enrollments
            if enrollment.course_overview and self.is_org(org, enrollment.course_overview.org)
        )
        mobile_available = (
            enrollment for enrollment in same_org
            if is_mobile_available_for_user(self.request.user, enrollment.course_overview)
        )
        not_hidden_for_experiments = (
            enrollment for enrollment in mobile_available
            if not self.hide_course_for_enrollment_fee_experiment(self.request.user, enrollment)
        )
        not_duration_limited = (
            enrollment for enrollment in not_hidden_for_experiments
            if check_course_expired(self.request.user, enrollment.course) == ACCESS_GRANTED
        )

        if api_version == API_V05:
            # for v0.5 don't return expired courses
            return list(not_duration_limited)
        else:
            # return all courses, with associated expiration
            return list(mobile_available)


@api_view(["GET"])
@mobile_view()
def my_user_info(request, api_version):
    """
    Redirect to the currently-logged-in user's info page
    """
    return redirect("user-detail", api_version=api_version, username=request.user.username)


@mobile_view(is_user=True)
class UserDeactivateLogoutView(views.APIView):
    """
        POST /api/mobile/v1/users/{username}/deactivate_logout/
        
    """ 
    http_method_names = ["post",]
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, *args, **kwargs)::
        """
        POST /api/mobile/v1/users/{username}/deactivate_logout/

        Marks the user as having no password set for deactivation purposes,
        and logs te user out.
        """    
        user_model = get_user_model()
        try:
            # Get the username from the request and check that it exists
            verify_user_password_response = self._verify_user_password(request)
            if verify_user_password_response.status_code != status.HTTP_204_NO_CONTENT:
                return Response(
                    data={
                        'msg': "密码异常",
                        'code': status.HTTP_403_FORBIDDEN
                    })
            with transaction.atomic():
                UserRetirementStatus.create_retirement(request.user)
                # Unlink LMS social auth accounts
                UserSocialAuth.objects.filter(user_id=request.user.id).delete()
                # Change LMS password & email
                user_email = request.user.email
                request.user.email = get_retired_email_by_email(request.user.email)
                request.user.save()
                _set_unusable_password(request.user)
                # TODO: Unlink social accounts & change password on each IDA.
                # Remove the activation keys sent by email to the user for account activation.
                Registration.objects.filter(user=request.user).delete()
                # Add user to retirement queue.
                # Delete OAuth tokens associated with the user.
                retire_dop_oauth2_models(request.user)
                retire_dot_oauth2_models(request.user)

                try:
                    # Send notification email to user
                    site = Site.objects.get_current()
                    notification_context = get_base_template_context(site)
                    notification_context.update({'full_name': request.user.profile.name})
                    notification = DeletionNotificationMessage().personalize(
                        recipient=Recipient(username='', email_address=user_email),
                        language=request.user.profile.language,
                        user_context=notification_context,
                    )
                    ace.send(notification)
                except Exception as exc:
                    log.exception('Error sending out deletion notification email')
                    raise

                # Log the user out.
                # TODO app获取接口成功后 退出登录，和WEB端不一样
                #logout(request)
            return Response(
                data={
                    'msg': _('操作成功'),
                    'code': status.HTTP_204_NO_CONTENT
                })
        except KeyError:
            return Response(
                data={
                    'msg': _('Username not specified.'),
                    'code': status.HTTP_404_NOT_FOUND
                })
        except user_model.DoesNotExist:
            return Response(
                data={
                    'msg': u'The user "{}" does not exist.'.format(request.user.username),
                    'code': status.HTTP_404_NOT_FOUND
                })
        except Exception as exc:  # pylint: disable=broad-except
            return Response(
                data={
                    'msg': text_type(exc),
                    'code': status.HTTP_500_INTERNAL_SERVER_ERROR
                })

    def _verify_user_password(self, request):
        """
        If the user is logged in and we want to verify that they have submitted the correct password
        for a major account change (for example, retiring this user's account).

        Args:
            request (HttpRequest): A request object where the password should be included in the POST fields.
        """
        try:
            self._check_excessive_login_attempts(request.user)
            user = authenticate(username=request.user.username, password=request.POST['password'], request=request)
            if user:
                if LoginFailures.is_feature_enabled():
                    LoginFailures.clear_lockout_counter(user)
                return Response(status=status.HTTP_204_NO_CONTENT)
            else:
                self._handle_failed_authentication(request.user)
        except AuthFailedError as err:
            return Response(text_type(err), status=status.HTTP_403_FORBIDDEN)
        except Exception as err:  # pylint: disable=broad-except
            return Response(u"Could not verify user password: {}".format(err), status=status.HTTP_400_BAD_REQUEST)

    def _check_excessive_login_attempts(self, user):
        """
        See if account has been locked out due to excessive login failures
        """
        if user and LoginFailures.is_feature_enabled():
            if LoginFailures.is_user_locked_out(user):
                raise AuthFailedError(_('Due to multiple login failures, the account is temporarily locked.'
                                        ' Please try again after 15 minutes.'))

    def _handle_failed_authentication(self, user):
        """
        Handles updating the failed login count, inactive user notifications, and logging failed authentications.
        """
        if user and LoginFailures.is_feature_enabled():
            LoginFailures.increment_lockout_counter(user)

        raise AuthFailedError(_('Email or password is incorrect.'))

