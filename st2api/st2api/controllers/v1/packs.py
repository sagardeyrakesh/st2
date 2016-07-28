# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pecan
from pecan.rest import RestController
from six.moves import http_client

from oslo_config import cfg

import st2common
from st2common import log as logging
import st2common.bootstrap.triggersregistrar as triggers_registrar
import st2common.bootstrap.sensorsregistrar as sensors_registrar
import st2common.bootstrap.actionsregistrar as actions_registrar
import st2common.bootstrap.aliasesregistrar as aliases_registrar
import st2common.bootstrap.policiesregistrar as policies_registrar
import st2common.bootstrap.runnersregistrar as runners_registrar
import st2common.bootstrap.rulesregistrar as rules_registrar
import st2common.bootstrap.ruletypesregistrar as rule_types_registrar
import st2common.bootstrap.configsregistrar as configs_registrar
from st2common.models.api.base import jsexpose
from st2common.models.api.base import BaseAPI
from st2api.controllers.resource import ResourceController
from st2api.controllers.v1.actionexecutions import ActionExecutionsControllerMixin
from st2common.constants.pack import SYSTEM_PACK_NAMES
from st2common.exceptions.db import StackStormDBObjectNotFoundError
from st2common.models.api.action import LiveActionCreateAPI
from st2common.models.api.pack import PackAPI
from st2common.persistence.pack import Pack
from st2common.persistence.pack import ConfigSchema
from st2common.persistence.reactor import SensorType
from st2common.persistence.reactor import TriggerType
from st2common.persistence.reactor import Trigger
from st2common.persistence.reactor import Rule
from st2common.persistence.action import Action
from st2common.persistence.action import ActionAlias
from st2common.rbac.types import PermissionType
from st2common.rbac.decorators import request_user_has_permission
from st2common.rbac.decorators import request_user_has_resource_db_permission
from st2common.services.triggers import cleanup_trigger_db_for_rule

__all__ = [
    'PacksController',
    'BasePacksController'
]

LOG = logging.getLogger(__name__)


BLOCKED_PACKS = frozenset(SYSTEM_PACK_NAMES)


class PackInstallRequestAPI(object):
    def __init__(self, packs=None):
        self.packs = packs

    def validate(self):
        assert isinstance(self.packs, list)

        return self

    def __json__(self):
        return vars(self)


class PackInstallAPI(BaseAPI):
    schema = {
        'type': 'object'
    }

    @classmethod
    def to_model(cls, doc):
        pass


class PackInstallController(ActionExecutionsControllerMixin, RestController):

    @jsexpose(body_cls=PackInstallRequestAPI, status_code=http_client.ACCEPTED)
    def post(self, pack_install_request):
        parameters = {
            'packs': pack_install_request.packs
        }

        new_liveaction_api = LiveActionCreateAPI(action='packs.install',
                                                 parameters=parameters,
                                                 user=None)

        execution = self._handle_schedule_execution(liveaction_api=new_liveaction_api)

        result = {
            'execution_id': execution.id
        }

        return PackInstallAPI(**result)


class PackUninstallController(ActionExecutionsControllerMixin, RestController):

    @jsexpose(body_cls=PackInstallRequestAPI, arg_types=[str], status_code=http_client.ACCEPTED)
    def post(self, pack_uninstall_request, ref_or_id=None):
        if ref_or_id:
            parameters = {
                'packs': [ref_or_id]
            }
        else:
            parameters = {
                'packs': pack_uninstall_request.packs
            }

        new_liveaction_api = LiveActionCreateAPI(action='packs.uninstall',
                                                 parameters=parameters,
                                                 user=None)

        execution = self._handle_schedule_execution(liveaction_api=new_liveaction_api)

        result = {
            'execution_id': execution.id
        }

        return PackInstallAPI(**result)


class PackRegisterController(RestController):

    @jsexpose()
    def post(self):
        types = ['runner', 'action', 'trigger', 'sensor', 'rule', 'rule_type', 'alias', 'policy_type', 'policy', 'config']

        result = {}

        if 'runner' in types:
            result['runners'] = runners_registrar.register_runner_types(experimental=True)
        if 'action' in types:
            result['actions'] = actions_registrar.register_actions(fail_on_failure=False)
        if 'trigger' in types:
            result['triggers'] = triggers_registrar.register_triggers(fail_on_failure=False)
        if 'sensor' in types:
            result['sensors'] = sensors_registrar.register_sensors(fail_on_failure=False)
        if 'rule_type' in types:
            result['rule_types'] = rule_types_registrar.register_rule_types()
        if 'rule' in types:
            result['rules'] = rules_registrar.register_rules(fail_on_failure=False)
        if 'alias' in types:
            result['aliases'] = aliases_registrar.register_aliases(fail_on_failure=False)
        if 'policy_type' in types:
            result['policy_types'] = policies_registrar.register_policy_types(st2common)
        if 'policy' in types:
            result['policy'] = policies_registrar.register_policies(fail_on_failure=False)
        if 'config' in types:
            result['config'] = configs_registrar.register_configs(fail_on_failure=False)

        return result


class PackDeregisterController(RestController):

    @jsexpose(body_cls=PackInstallRequestAPI, arg_types=[str], status_code=http_client.ACCEPTED)
    def post(self, pack_deregister_request, ref_or_id=None):
        if ref_or_id:
            packs = [ref_or_id]
        else:
            packs = pack_deregister_request.packs

        intersection = BLOCKED_PACKS & frozenset(packs)
        if len(intersection) > 0:
            names = ', '.join(list(intersection))
            raise ValueError('System packs can not be deregistred: %s.' % (names))

        for pack in packs:
            LOG.debug('Removing pack %s.', pack)
            for access_cls in [SensorType, TriggerType, Trigger, Action, Rule, ActionAlias]:
                deleted_entities = self._delete_pack_db_objects(pack=pack, access_cls=access_cls)
                if access_cls is Rule:
                    for rule_db in deleted_entities:
                        cleanup_trigger_db_for_rule(rule_db=rule_db)

            try:
                pack_db = Pack.get_by_name(value=pack)
            except StackStormDBObjectNotFoundError as e:
                LOG.exception('Pack DB object not found: %s', pack)
            else:
                try:
                    Pack.delete(pack_db)
                except Exception:
                    LOG.exception('Failed to remove DB object %s.', pack_db)
                    raise

            try:
                config_schema_db = ConfigSchema.get_by_pack(value=pack)
            except StackStormDBObjectNotFoundError as e:
                LOG.exception('ConfigSchemaDB object not found: %s', pack)
            else:
                try:
                    ConfigSchema.delete(config_schema_db)
                except Exception:
                    LOG.exception('Failed to remove DB object %s.', config_schema_db)
                    raise

            LOG.info('Removed pack %s.', pack)

    def _delete_pack_db_objects(self, pack, access_cls):
        db_objs = access_cls.get_all(pack=pack)

        deleted_objs = []

        for db_obj in db_objs:
            try:
                access_cls.delete(db_obj)
                deleted_objs.append(db_obj)
            except:
                LOG.exception('Failed to remove DB object %s.', db_obj)

        return deleted_objs


class BasePacksController(ResourceController):
    model = PackAPI
    access = Pack

    def _get_one_by_ref_or_id(self, ref_or_id, exclude_fields=None):
        LOG.info('GET %s with ref_or_id=%s', pecan.request.path, ref_or_id)

        instance = self._get_by_ref_or_id(ref_or_id=ref_or_id, exclude_fields=exclude_fields)

        if not instance:
            msg = 'Unable to identify resource with ref_or_id "%s".' % (ref_or_id)
            pecan.abort(http_client.NOT_FOUND, msg)
            return

        from_model_kwargs = self._get_from_model_kwargs_for_request(request=pecan.request)
        result = self.model.from_model(instance, **from_model_kwargs)
        LOG.debug('GET %s with ref_or_id=%s, client_result=%s', pecan.request.path, ref_or_id,
                  result)

        return result

    def _get_by_ref_or_id(self, ref_or_id, exclude_fields=None):
        resource_db = self._get_by_id(resource_id=ref_or_id, exclude_fields=exclude_fields)

        if not resource_db:
            # Try ref
            resource_db = self._get_by_ref(ref=ref_or_id, exclude_fields=exclude_fields)

        return resource_db

    def _get_by_ref(self, ref, exclude_fields=None):
        """
        Note: In this case "ref" is pack name and not StackStorm's ResourceReference.
        """
        resource_db = self.access.query(ref=ref, exclude_fields=exclude_fields).first()
        return resource_db


class PacksController(BasePacksController):
    from st2api.controllers.v1.packviews import PackViewsController

    model = PackAPI
    access = Pack
    supported_filters = {
        'name': 'name',
        'ref': 'ref'
    }

    query_options = {
        'sort': ['ref']
    }

    # Nested controllers
    install = PackInstallController()
    uninstall = PackUninstallController()
    register = PackRegisterController()
    deregister = PackDeregisterController()
    views = PackViewsController()

    @request_user_has_permission(permission_type=PermissionType.PACK_LIST)
    @jsexpose()
    def get_all(self, **kwargs):
        return super(PacksController, self)._get_all(**kwargs)

    @request_user_has_resource_db_permission(permission_type=PermissionType.PACK_VIEW)
    @jsexpose(arg_types=[str])
    def get_one(self, ref_or_id):
        return self._get_one_by_ref_or_id(ref_or_id=ref_or_id)
