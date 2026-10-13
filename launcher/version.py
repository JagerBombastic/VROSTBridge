"""Release labels. core/version.hpp, packaging and installer scripts mirror
these values; tests/test_rc2_updates.py (VersionConsistency) keeps every copy consistent.
Version labels are independent of protocol identity (see PROJECT_SPEC.md)."""
CLIENT_VERSION = '1.0.0.11'
HOST_VERSION = '1.0.0.11'


def role_version(role):
    return CLIENT_VERSION if role == 'client' else HOST_VERSION
