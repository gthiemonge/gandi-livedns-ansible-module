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
module: gandi_livedns
author:
- Gregory Thiemonge <gregory.thiemonge@gmail.com>
requirements:
- python >= 2.6
version_added: "1.0"
short_description: Manage Gandi LiveDNS records
description:
- "Manages dns records via the Gandi LiveDNS API, see the docs: U(https://doc.livedns.gandi.net/)"
options:
  api_key:
    description:
    - Account API token.
    type: str
    required: true
  record:
    description:
    - Record to add.
    - Default is C(@) (e.g. the zone name).
    type: str
    required: true
    aliases: [ name ]
  state:
    description:
    - Whether the record(s) should exist or not.
    type: str
    choices: [ absent, present ]
    default: present
  ttl:
    description:
    - The TTL to give the new record.
    type: int
    default: 10800
  type:
    description:
      - The type of DNS record to create.
    type: str
    required: true
    choices: [ A, AAAA, ALIAS, CAA, CDS, CNAME, DNAME, DS, KEY, LOC, MX, NS, PTR, SPF, SRV, SSHFP, TLSA, TXT, WKS ]
  values:
    description:
    - The record values.
    - Required for C(state=present).
    type: list
    aliases: [ content ]
  zone:
    description:
    - The name of the Zone to work with (e.g. "example.com").
    - The Zone must already exist.
    type: str
    required: true
  domain:
    description:
    - The name of the Domain to work with (e.g. "example.com").
    type: str
'''

EXAMPLES = r'''
- name: Create a test A record to point to 127.0.0.1 in the my.com zone.
  gandi_livedns:
    zone: my.com
    record: test
    type: A
    values:
    - 127.0.0.1
    api_key: dummyapitoken
  register: record

- name: Create a mail CNAME record to www.my.com domain
  gandi_livedns:
    domain: my.com
    type: CNAME
    record: mail
    values:
    - www
    api_key: dummyapitoken
    state: present

- name: Change its TTL
  gandi_livedns:
    domain: my.com
    type: CNAME
    values:
    - www
    ttl: 10800
    api_key: dummyapitoken
    state: present

- name: Delete the record
  gandi_livedns:
    domain: my.com
    type: CNAME
    record: mail
    api_key: dummyapitoken
    state: absent
'''

RETURN = r'''
record:
    description: A dictionary containing the record data.
    returned: success, except on record deletion
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
        400: 'Bad request',
        401: 'Permission denied',
        404: 'Resource not found',
    }

    def __init__(self, module):
        self.module = module
        self.api_key = module.params['api_key']
        self.record = lowercase_string(module.params['record'])
        self.state = module.params['state']
        self.ttl = module.params['ttl']
        self.type = module.params['type']
        self.values = module.params['values']
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
        if info['status'] >= 400 and (info['status'] != 404 or error_on_404):
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

    def build_result(self, result):
        if result is None:
            return None

        res = {}
        for k in ('name', 'type', 'ttl', 'values'):
            v = result.get('rrset_' + k, None)
            if v is not None:
                res[k] = v

        if self.zone:
            res['zone'] = self.zone
        else:
            res['domain'] = self.domain

        return res

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

    def create_record(self, name, type, values, ttl,
                      zone_id=None, domain=None):
        if zone_id:
            url = '/zones/%s' % (zone_id)
        else:
            url = '/domains/%s' % (domain)

        url += '/records'
        new_record = {
            'rrset_name': name,
            'rrset_type': type,
            'rrset_values': values,
            'rrset_ttl': ttl,
        }
        record, status = self._gandi_api_call(
            url,
            method='POST',
            payload=new_record)

        if status in (201,):
            return new_record

        return None

    def update_record(self, name, type, values, ttl,
                      zone_id=None, domain=None):
        if zone_id:
            url = '/zones/%s' % (zone_id)
        else:
            url = '/domains/%s' % (domain)

        url += '/records/%s/%s' % (name, type)
        new_record = {
            'rrset_values': values,
            'rrset_ttl': ttl,
        }
        record, status = self._gandi_api_call(
            url,
            method='PUT',
            payload=new_record)
        return record

    def delete_record(self, name, type, zone_id=None, domain=None):
        if self.type is None or self.record is None:
            self.module.fail_json(msg="You must provide a type and a record to delete a record")

        if self.values is not None:
            self.module.fail_json(msg="You cannot provide a value when deleting a record")

        if zone_id:
            url = '/zones/%s' % (zone_id)
        else:
            url = '/domains/%s' % (domain)
        url += '/records/%s/%s' % (name, type)

        record, status = self._gandi_api_call(
            url,
            method='DELETE')

    def delete_dns_records(self):
        if self.zone:
            zone_id = self._get_zone_id(self.zone)
        else:
            zone_id = None

        records = self.get_records(self.record, self.type, zone_id=zone_id, domain=self.domain)

        if records:
            self.changed = True
            if not self.module.check_mode:
                result = self.delete_record(self.record, self.type,
                                            zone_id=zone_id, domain=self.domain)

        return self.changed

    def ensure_dns_record(self):
        new_record = {
            "type": self.type,
            "name": self.record,
            "values": self.values,
            "ttl": self.ttl
        }

        if self.zone:
            zone_id = self._get_zone_id(self.zone)
        else:
            zone_id = None

        records = self.get_records(self.record, self.type,
                                   zone_id=zone_id, domain=self.domain)

        if records:
            record = records[0]

            do_update = False
            if self.ttl is not None and record['rrset_ttl'] != self.ttl:
                do_update = True
            if self.values is not None and set(record['rrset_values']) != set(self.values):
                do_update = True

            if do_update:
                if self.module.check_mode:
                    result = new_record
                else:
                    self.update_record(self.record, self.type, self.values, self.ttl,
                                       zone_id=zone_id, domain=self.domain)

                    records = self.get_records(self.record, self.type,
                                               zone_id=zone_id, domain=self.domain)
                    result = records[0]
                self.changed = True
                return result, self.changed
            else:
                return record, self.changed

        if self.module.check_mode:
            result = new_record
        else:
            result = self.create_record(self.record, self.type, self.values, self.ttl,
                                        zone_id=zone_id, domain=self.domain)
        self.changed = True
        return result, self.changed


def main():
    module = AnsibleModule(
        argument_spec=dict(
            api_key=dict(type='str', required=True, no_log=True),
            record=dict(type='str', default='@', aliases=['name']),
            state=dict(type='str', default='present', choices=['absent', 'present']),
            ttl=dict(type='int', default=10800),
            type=dict(type='str', choices=['A', 'AAAA', 'ALIAS', 'CAA', 'CDS', 'CNAME', 'DNAME', 'DS', 'KEY', 'LOC', 'MX', 'NS', 'PTR', 'SPF', 'SRV', 'SSHFP', 'TLSA', 'TXT', 'WKS']),
            values=dict(type='list'),
            zone=dict(type='str'),
            domain=dict(type='str'),
        ),
        supports_check_mode=True,
        required_if=[
            ('state', 'present', ['record', 'type', 'values']),
            ('state', 'absent', ['record', 'type']),
        ],
    )

    if not module.params['zone'] and not module.params['domain']:
        module.fail_json(msg="At least one of zone and domain parameters need to be defined.")

    gandi_api = GandiAPI(module)

    if gandi_api.state == 'present':
        result, changed = gandi_api.ensure_dns_record()
        module.exit_json(changed=changed, result={'record': gandi_api.build_result(result)})
    else:
        changed = gandi_api.delete_dns_records()
        module.exit_json(changed=changed)


if __name__ == '__main__':
    main()
