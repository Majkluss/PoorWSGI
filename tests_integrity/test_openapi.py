from os import environ
from os.path import dirname, join, pardir
from sys import executable
from subprocess import Popen
from time import sleep
from socket import socket, error as SocketError

from pytest import fixture
from requests import Session

from . support import check_url, check_api
from . openapi import response_validator_json

VALIDATOR = response_validator_json(
    join(dirname(__file__), pardir, "examples/openapi.json"))


@fixture(scope="module")
def url(request):
    url = environ.get("TEST_OPENAPI_URL", "").strip('/')
    if url:
        yield url
        return

    process = None
    print("Starting wsgi application...")
    if request.config.getoption("--with-uwsgi"):
        process = Popen(["uwsgi", "--plugin", "python3",
                         "--http-socket", "localhost:8080", "--wsgi-file",
                         join(dirname(__file__), pardir,
                              "examples/openapi3.py")])
    else:
        process = Popen([executable,
                         join(dirname(__file__), pardir,
                              "examples/openapi3.py")])

    assert process is not None
    connect = False
    for i in range(30):
        sck = socket()
        try:
            sck.connect(("localhost", 8080))
            connect = True
            break
        except SocketError:
            sleep(0.1)
        finally:
            sck.close()
    if not connect:
        process.kill()
        raise RuntimeError("Server not started in 3 seconds")

    yield "http://localhost:8080"  # server is running
    process.kill()


@fixture
def session(url):
    session = Session()
    check_url(url+"/login", session=session, status_code=204)
    return session


class TestOpenAPI():
    def test_plain_text(self, url):
        res = check_api(url+"/plain_text",
                        headers={'Accept': 'text/plain'},
                        response_validator=VALIDATOR)
        assert res.headers["Content-Type"] == "text/plain"

    def test_json_arg_integer(self, url):
        res = check_api(url+"/json/42",
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR,
                        path_pattern=url+"/json/{arg}")
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("arg") == '42'

    def test_json_arg_float(self, url):
        res = check_api(url+"/json/3.14",
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR,
                        path_pattern=url+"/json/{arg}")
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("arg") == '3.14'

    def test_json_arg_string(self, url):
        res = check_api(url+"/json/ok",
                        status_code=400,
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR,
                        path_pattern=url+"/json/{arg}")
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("error") is not None

    def test_json_post_unicode(self, url):
        data = "Česká Lípa"
        res = check_api(url+"/json", status_code=418,
                        method="POST", json=data,
                        response_validator=VALIDATOR)
        assert res.json()["request"] == data

    def test_json_post_unicode_struct(self, url):
        data = dict(city="Česká Lípa")
        res = check_api(url+"/json", status_code=418,
                        method="PUT", json=data,
                        response_validator=VALIDATOR)
        assert res.json()["request"] == data

    def test_arg_integer(self, url):
        res = check_api(url+"/arg/42",
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR)
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("integer_arg") == 42

    def test_arg_float(self, url):
        res = check_api(url+"/arg/3.14",
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR)
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("float_arg") == 3.14

    def test_arg_string(self, url):
        res = check_api(url+"/arg/ok",
                        status_code=404,
                        headers={'Accept': 'application/json'},
                        response_validator=VALIDATOR)
        assert res.headers["Content-Type"] == "application/json"
        data = res.json()
        assert data.get("error") is not None

    def test_native_not_found(self, url):
        check_url(url+"/notexists_url", status_code=404)

    def test_native_method_not_allowed(self, url):
        check_url(url+"/plain_text", method="DELETE", status_code=405)

    def test_secrets_cookie(self, url, session):
        check_api(url+"/check/login", method="GET", session=session,
                  response_validator=VALIDATOR)

    def test_secrets_api_key(self, url):
        check_api(url+"/check/api-key", method="GET",
                  headers={"API-Key": "xxx"},
                  response_validator=VALIDATOR)

    def test_secrets_no_api_key(self, url):
        check_api(url+"/check/api-key", method="GET",
                  status_code=401,
                  response_validator=VALIDATOR)
