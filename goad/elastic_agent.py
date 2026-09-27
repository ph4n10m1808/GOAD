"""Validate and save per-instance Fleet options, with hidden token prompting."""
import getpass
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import tempfile
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Safe to display: messages never include user-supplied secrets."""


def parse_options(command):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ConfigurationError('Use elastic_agent [--ip HOST] [--token TOKEN] [--crt PATH] [--version VERSION].')

    parser = Parser(add_help=False, allow_abbrev=False)
    for flag in ('ip', 'token', 'crt', 'version'):
        parser.add_argument('--' + flag)
    try:
        return vars(parser.parse_args(shlex.split(command)[1:]))
    except ValueError:
        raise ConfigurationError('Invalid Elastic Agent options or quoting.') from None


def validate_ca(pem):
    if not isinstance(pem, str) or 'PRIVATE KEY' in pem or '-----BEGIN CERTIFICATE-----' not in pem:
        raise ConfigurationError('Select a PEM CA certificate (.crt), without a private key.')
    if not re.fullmatch(r'\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+', pem):
        raise ConfigurationError('The .crt file must contain only PEM certificates and whitespace.')
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
        if not context.cert_store_stats()['x509_ca']:
            raise ConfigurationError('The .crt file must contain a CA certificate, not only a server certificate.')
    except ssl.SSLError:
        raise ConfigurationError('The .crt file does not contain a valid PEM CA certificate.') from None


def fleet_url(value):
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('Use a Fleet Server IP/hostname or https://host:port URL.')
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
        host = f'[{address}]' if address.version == 6 else str(address)
    except ValueError:
        if (re.fullmatch(r'[0-9.]+', host) or len(host) > 253
                or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part)
                           for part in host.split('.'))):
            raise ValueError('Invalid Fleet Server IP or hostname.')
    port = parsed.port if parsed.port is not None else 8220
    if not 1 <= port <= 65535:
        raise ValueError('Fleet Server port must be between 1 and 65535.')
    return f'https://{host}:{port}'


def validate(values):
    values = dict(values)
    values['elastic_agent_fleet_url'] = fleet_url(values.get('elastic_agent_fleet_url', ''))
    token = values.get('elastic_agent_enrollment_token', '')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_+/=-]+', token):
        raise ValueError('A non-empty Fleet enrollment token is required (no whitespace).')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', values.get('elastic_agent_version', '')):
        raise ValueError('Enter an exact Elastic Agent version, for example 8.19.0.')
    if values.get('elastic_agent_insecure', False) is not False:
        raise ConfigurationError('TLS verification must remain enabled.')
    validate_ca(values.get('elastic_agent_ca_pem'))
    return values


def configure(instance_path, force=False, options=None):
    path = Path(instance_path) / 'elastic_agent_options.yml'
    previous = json.loads(path.read_text())['all']['children']['elastic_agent_targets']['vars'] if path.exists() else {}
    if previous and not force:
        validate(previous)
        return

    options = options or {}

    def ask(label, key, option, fallback=''):
        if options.get(option) is not None:
            return options[option]
        default = previous.get(key, fallback)
        answer = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip()
        return answer or default

    values = {
        'elastic_agent_fleet_url': ask('Fleet Server IP/hostname or HTTPS URL', 'elastic_agent_fleet_url', 'ip'),
        'elastic_agent_enrollment_token': options.get('token') if options.get('token') is not None else getpass.getpass(
            'Fleet enrollment token (hidden; blank keeps saved token): '
        ).strip() or previous.get('elastic_agent_enrollment_token', ''),
        'elastic_agent_version': ask('Elastic Agent version', 'elastic_agent_version', 'version', '8.19.16'),
    }
    crt = options.get('crt')
    if crt is None:
        crt = input('Local CA .crt path (blank keeps saved CA): ').strip()
    if crt:
        try:
            values['elastic_agent_ca_pem'] = Path(crt).expanduser().read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise ConfigurationError('Cannot read the CA .crt file. Check its local path and permissions.') from None
        values['elastic_agent_ca_source'] = crt
    else:
        values['elastic_agent_ca_pem'] = previous.get('elastic_agent_ca_pem', '')
        values['elastic_agent_ca_source'] = previous.get('elastic_agent_ca_source', '')
    values['elastic_agent_insecure'] = False
    values = validate(values)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.elastic-agent-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'all': {'children': {'elastic_agent_targets': {'vars': values}}}}, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
