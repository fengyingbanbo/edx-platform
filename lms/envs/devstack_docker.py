""" Overrides for Docker-based devstack. """

from .devstack import *  # pylint: disable=wildcard-import, unused-wildcard-import

# for SMS
SMS_API = AUTH_TOKENS.get('SMS_API', '')
SMS_API_BY_LINKGROUP = AUTH_TOKENS.get('SMS_API_BY_LINKGROUP', '')
SMS_API_URL = ENV_TOKENS.get('SMS_API_URL', '')
SMS_API_URL_BY_LINKGROUP = ENV_TOKENS.get('SMS_API_URL_BY_LINKGROUP', '')

if FEATURES.get('ENABLE_MEMBERSHIP_INTEGRATION', False):
    INSTALLED_APPS.append('membership')
    REST_FRAMEWORK.update({'EXCEPTION_HANDLER': 'membership.utils.customer_exception_handler'})

######################## Professors ###########################
if FEATURES.get('ENABLE_PROFESSORS'):
    INSTALLED_APPS.append('professors')

############################ WEIXINAPPID_AND_WEIXINAPPSECRET #########################
WEIXINAPPID = ''
WEIXINAPPSECRET = ''
WEIXINAPPID = ENV_TOKENS.get('WEIXINAPPID', WEIXINAPPID)
WEIXINAPPSECRET = ENV_TOKENS.get('WEIXINAPPSECRET', WEIXINAPPSECRET)
