#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2019 Gregory Thiemonge <gregory.thiemonge@gmail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = r'''
---
module: gandi_livedns_facts
author:
- Gregory Thiemonge <gregory.thiemonge@gmail.com>
requirements:
- python >= 2.6
version_added: "1.0"
short_description: Retrieve Gandi LiveDNS facts
description:
- "Retrieves dns records via the Gandi LiveDNS API, see the docs: U(https://doc.livedns.gandi.net/)"
options:
  api_key:
    description:
    - Account API token.
    type: str
    required: true
  record:
    description:
    - Record to retrieve.
    - Absence of record retrieves all records of zone or domain.
    type: str
    aliases: [ name ]
  type:
    description:
    - The type of DNS records to retrieve.
    - Absence of type retrieves all type of records.
    type: str
    choices: [ A, AAAA, ALIAS, CAA, CDS, CNAME, DNAME, DS, KEY, LOC, MX, NS, PTR, SPF, SRV, SSHFP, TLSA, TXT, WKS ]
  zone:
    description:
    - The name of the Zone to work with (e.g. "example.com").
    - The Zone must already exist.
    type: str
  domain:
    description:
    - The name of the Domain to work with (e.g. "example.com").
    type: str
'''

EXAMPLES = r'''
- name: Get test A record for my.com domain
  gandi_livedns_facts:
    domain: my.com
    record: test
    type: A
    api_key: dummyapitoken

- name: Get all records of the my.com zone
  gandi_livedns_facts:
    zone: my.com
    api_key: dummyapitoken

- name: Get test records for my.com domain
  gandi_livedns_facts:
    domain: my.com
    record: test
    api_key: dummyapitoken
'''

RETURN = r'''
gandi_livedns_facts:
    description: A list containing the records data.
    returned: success, except on invalid parameters
    type: complex
    contains:
        values:
            description: The record content (details depend on record type).
            returned: success
            type: list
            sample:
            - 192.0.2.91
            - 192.0.2.92
        name:
            description: The record name.
            returned: success
            type: str
            sample: www
        ttl:
            description: The time-to-live for the record.
            returned: success
            type: int
            sample: 300
        type:
            description: The record type.
            returned: success
            type: str
            sample: A
        domain:
            description: The domain associated with the record.
            returned: success
            type: str
            sample: my.com
        zone:
            description: The zone associated with the record.
            returned: success
            type: str
            sample: my.com
'''

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native, to_text
from ansible.module_utils.urls import fetch_url


def lowercase_string(param):
    if not isinstance(param, str):
        return param
    return param.lower()


class GandiAPI(object):

    api_endpoint = 'https://dns.api.gandi.net/api/v5'
    changed = False

    error_strings = {
        401: 'Permission denied',
        404: 'Resource not found',
    }

    def __init__(self, module):
        self.module = module
        self.api_key = module.params['api_key']
        self.record = lowercase_string(module.params['record'])
        self.type = module.params['type']
        self.zone = module.params['zone']
        self.domain = lowercase_string(module.params['domain'])

    def _gandi_api_call(self, api_call, method='GET', payload=None, error_on_404=True):
        headers = {'X-Api-Key': self.api_key,
                   'Content-Type': 'application/json'}
        data = None
        if payload:
            try:
                data = json.dumps(payload)
            except Exception as e:
                self.module.fail_json(msg="Failed to encode payload as JSON: %s " % to_native(e))

        resp, info = fetch_url(self.module,
                               self.api_endpoint + api_call,
                               headers=headers,
                               data=data,
                               method=method)

        error_msg = ''
        if info['status'] >= 400:
            err_s = self.error_strings.get(info['status'], '')

            error_msg = "API error {0}; Status: {1}; Method: {2}: Call: {3}".format(err_s, info['status'], method, api_call)

        result = None
        try:
            content = resp.read()
        except AttributeError:
            content = None

        if content:
            try:
                result = json.loads(to_text(content, errors='surrogate_or_strict'))
            except (getattr(json, 'JSONDecodeError', ValueError)) as e:
                error_msg += "; Failed to parse API response with error {0}: {1}".format(to_native(e), content)

        if error_msg:
            self.module.fail_json(msg=error_msg)

        return result, info['status']

    def build_results(self, results):
        ret = []

        if results is None:
            return None

        for res in results:
            d = {}
            for k in ('name', 'type', 'ttl', 'values'):
                v = res.get('rrset_' + k, None)
                if v is not None:
                    d[k] = v
            if self.zone:
                d['zone'] = self.zone
            else:
                d['domain'] = self.domain
            ret.append(d)

        return ret

    def _get_zone_id(self, zone_name):
        for z in self.get_zones():
            if z['name'] == zone_name:
                return z['uuid']
        self.module.fail_json(msg="No zone found with name {0}".format(zone_name))

    def get_zones(self):
        zones, status = self._gandi_api_call('/zones')
        return zones

    def get_records(self, name, type, zone_id=None, domain=None):
        if zone_id:
            url = '/zones/%s' % (zone_id)
        else:
            url = '/domains/%s' % (domain)

        url += '/records'
        if name:
            url += '/%s' % (name)
            if type:
                url += '/%s' % (type)

        records, status = self._gandi_api_call(url, error_on_404=False)

        if status == 404:
            return None

        if not isinstance(records, list):
            records = [records]

        # filter by type if name is not set
        if not name and type:
            records = [r
                       for r in records
                       if r['rrset_type'] == type]

        return records

    def get_dns_records(self, **kwargs):

        if self.zone:
            zone_id = self._get_zone_id(self.zone)
        else:
            zone_id = None

        record = self.get_records(self.record, self.type, zone_id=zone_id, domain=self.domain)

        return record

def main():
    module = AnsibleModule(
        argument_spec=dict(
            api_key=dict(type='str', required=True, no_log=True),
            record=dict(type='str', aliases=['name']),
            type=dict(type='str', choices=['A', 'AAAA', 'ALIAS', 'CAA', 'CDS', 'CNAME', 'DNAME', 'DS', 'KEY', 'LOC', 'MX', 'NS', 'PTR', 'SPF', 'SRV', 'SSHFP', 'TLSA', 'TXT', 'WKS']),
            zone=dict(type='str'),
            domain=dict(type='str'),
        ),
        supports_check_mode=True,
    )

    if not module.params['zone'] and not module.params['domain']:
        module.fail_json(msg="At least one of zone and domain parameters need to be defined.")

    gandi_api = GandiAPI(module)

    results = gandi_api.get_dns_records()

    facts = {'records': gandi_api.build_results(results)}

    module.exit_json(changed=False, ansible_facts={'gandi_livedns_facts': facts})

if __name__ == '__main__':
    main()
