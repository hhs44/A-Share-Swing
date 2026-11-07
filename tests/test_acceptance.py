from importlib.metadata import PackageNotFoundError

from ashare_swing import acceptance


def test_missing_dependency_keeps_diagnostic_versions(monkeypatch):
    def lookup(package):
        if package == 'akshare':
            raise PackageNotFoundError(package)
        return 'test-version'

    monkeypatch.setattr(acceptance, 'version', lookup)
    assert acceptance.dependency_versions() == {
        'akshare': None,
        'pandas': 'test-version',
        'numpy': 'test-version',
        'pytest': 'test-version',
    }
