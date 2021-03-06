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

import re

from st2common.constants.pack import PACK_REF_WHITELIST_REGEX

__all__ = [
    'get_pack_ref_from_metadata'
]


def get_pack_ref_from_metadata(metadata, pack_directory_name=None):
    """
    Utility function which retrieves pack "ref" attribute from the pack metadata file.

    If this attribute is not provided, an attempt is made to infer "ref" from the "name" attribute.

    :rtype: ``str``
    """
    pack_ref = None

    # The rules for the pack ref are as follows:
    # 1. If ref attribute is available, we used that
    # 2. If pack_directory_name is available we use that (this only applies to packs
    # which are in sub-directories)
    # 2. If attribute is not available, but pack name is and pack name meets the valid name
    # criteria, we use that
    if metadata.get('ref', None):
        pack_ref = metadata['ref']
    elif pack_directory_name and re.match(PACK_REF_WHITELIST_REGEX, pack_directory_name):
        pack_ref = pack_directory_name
    else:
        if re.match(PACK_REF_WHITELIST_REGEX, metadata['name']):
            pack_ref = metadata['name']
        else:
            raise ValueError('Pack name "%s" contains invalid characters and "ref" '
                             'attribute is not available' % (metadata['name']))

    return pack_ref
