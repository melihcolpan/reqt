import ssl

import aiohttp

import reqstorm


async def test_certificates_are_verified_by_default(tls_server):
    (result,) = await reqstorm.fetch_all([f"{tls_server}/ok"])
    assert isinstance(result.error, aiohttp.ClientConnectorCertificateError)
    assert isinstance(result.error.__cause__ or result.error.certificate_error, ssl.SSLError)


async def test_verification_can_be_disabled(tls_server):
    (result,) = await reqstorm.fetch_all([f"{tls_server}/ok"], verify_ssl=False)
    assert result.ok


async def test_custom_ssl_context(tls_server, trusted_context):
    (result,) = await reqstorm.fetch_all([f"{tls_server}/ok"], ssl=trusted_context)
    assert result.ok
