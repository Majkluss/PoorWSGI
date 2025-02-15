"""
Poor WSGI Response classes.

:Exceptions:    HTTPException
:Classes:       Response, JSONResponse, FileResponse, GeneratorResponse,
                StrGeneratorResponse, EmptyResponse, RedirectResponse
:Functions:     make_response, redirect, abort
"""
from http.client import responses
from io import BytesIO, IOBase, BufferedIOBase, TextIOBase
from os import access, R_OK, fstat
from os.path import getctime
from logging import getLogger
from json import dumps
from inspect import stack
from datetime import datetime
from typing import Union, Callable, Iterable, BinaryIO, Optional

import mimetypes

try:
    from simplejson import JSONEncoder
    JSON_GENERATOR = True
except ImportError:
    JSON_GENERATOR = False

from poorwsgi.state import DECLINED, HTTP_OK, HTTP_NO_CONTENT, \
    HTTP_MOVED_PERMANENTLY, HTTP_MOVED_TEMPORARILY, HTTP_I_AM_A_TEAPOT, \
    HTTP_NOT_MODIFIED, deprecated
from poorwsgi.headers import Headers, HeadersList, \
    time_to_http, datetime_to_http

log = getLogger('poorwsgi')
# not in http.client.responses
responses[HTTP_I_AM_A_TEAPOT] = "I'm a teapot"

# pylint: disable=unsubscriptable-object
# pylint: disable=consider-using-f-string

NOT_MODIFIED_DENY = {
    'Content-Encoding', 'Content-Language', 'Content-Length', 'Content-MD5',
    'Content-Range', 'Content-Type'}
NOT_MODIFIED_ONE_OF_REQUIRED = {
    'Content-Location', 'Date', 'ETag', 'Vary'
    }


class IBytesIO(BytesIO):
    """Class for returning bytes when is iterate."""

    def read_kilo(self):
        """Read 1024 bytes from buffer."""
        return self.read(1024)

    def __iter__(self):
        """Iterate object by 1024 bytes."""
        return iter(self.read_kilo, b'')


class BaseResponse:
    """Base class for response."""

    def __init__(self, content_type: str = "",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        assert isinstance(content_type, str), \
            "content_type is not string but `%s`" % content_type
        assert isinstance(status_code, int), \
            "status_code is not number but `%s`" % status_code

        # String. The content type. Another way to set content_type is via
        # headers_out object property. Default is text/html; charset=utf-8
        self.content_type = content_type

        # A Headers object representing the headers to be sent to the client.
        if isinstance(headers, Headers):
            self.__headers = headers
        elif headers is None:
            self.__headers = Headers(
                (("X-Powered-By", "Poor WSGI for Python"),))
        else:
            self.__headers = Headers(headers)

        # Status. One of state.HTTP_* values.
        self.__status_code = status_code
        self.__reason = responses[self.__status_code]
        self.__done = False

    @property
    def status_code(self):
        """Http status code, which is **state.HTTP_OK (200)** by default.

        If you want to set this variable (which is very good idea in http_state
        handlers), it is good solution to use some of ``HTTP_`` constant from
        state module.
        """
        return self.__status_code

    @status_code.setter
    def status_code(self, value: int):
        if value not in responses:
            raise ValueError("Bad response status %s" % value)
        self.__status_code = value
        self.__reason = responses[self.__status_code]

    @property
    def reason(self):
        """HTTP response is set automatically with setting status_code.

        Setting response message is not good idea, but you can create
        own class based on Response, when you can override status_code setter.
        """
        return self.__reason

    @property
    def content_length(self):
        """Return content_length of response.

        That is size of internal buffer.
        """
        return 0

    @property
    def data(self):
        """Return data content."""
        return b''

    @property
    def headers(self):
        """Reference to output headers object."""
        return self.__headers

    @headers.setter
    def headers(self, value: Union[Headers, HeadersList]):
        if isinstance(value, Headers):
            self.__headers = value
        else:
            self.__headers = Headers(value)

    def add_header(self, name: str, value: str, **kwargs):
        """Call Headers.add_header on headers object."""
        self.__headers.add_header(name, value, **kwargs)

    def __start_response__(self, start_response: Callable):
        if self.__status_code == 304:
            # Not Modified SHOULD NOT include other representation headers
            # https://www.rfc-editor.org/rfc/rfc9110.html#name-304-not-modified
            # pylint: disable=too-many-boolean-expressions
            _headers = set(self.__headers.keys())
            if _headers.intersection(NOT_MODIFIED_DENY):
                log.warning(
                        'Some representation header in Not Modified response')
            if not _headers.intersection(NOT_MODIFIED_ONE_OF_REQUIRED):
                log.warning(
                        'Missing any required header in Not Modified response')
        else:
            if self.content_type \
                    and not self.__headers.get('Content-Type'):
                self.__headers.add('Content-Type', self.content_type)

            if self.content_length \
                    and not self.__headers.get('Content-Length'):
                self.__headers.add('Content-Length',
                                   str(self.content_length))

        start_response(
            "%d %s" % (self.__status_code, self.__reason),
            list(self.__headers.items()))

    def __end_of_response__(self):
        """Method **for internal use only!**.

        This method was called from Application object at the end of request
        for returning right value to wsgi server.
        """
        # pylint: disable=no-self-use
        return b''

    def __call__(self, start_response: Callable):
        if self.__done:
            raise RuntimeError('Response can be used only once!')
        try:
            self.__start_response__(start_response)
            return self.__end_of_response__()
        finally:
            self.__done = True


class Response(BaseResponse):
    """HTTP Response object.

    This is base Response object which is process with PoorWSGI application.

    As Response uses BytesIO as internal cache, which is closed by WSGI
    server, **response can be used only once!**.
    """
    __buffer: BufferedIOBase

    def __init__(self, data: Union[str, bytes] = b'',
                 content_type: str = "text/html; charset=utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        assert isinstance(data, (str, bytes)), \
            "data is not string or bytes but %s" % type(data)

        super().__init__(content_type, headers, status_code)

        # The content length header was set automatically from buffer length.
        if isinstance(data, str):
            data = data.encode("utf-8")

        self.__buffer = IBytesIO(data)
        self.__content_length = len(data)

    @property
    def content_length(self):
        return self.__content_length

    @property
    def data(self):
        self.__buffer.seek(0)
        return self.__buffer.read()

    def write(self, data: Union[str, bytes]):
        """Write data to internal buffer."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.__content_length += len(data)
        self.__buffer.write(data)

    def __end_of_response__(self):
        self.__buffer.seek(0)
        return self.__buffer


class JSONResponse(Response):
    """Simple application/json response.

    ** kwargs from constructor are serialized to json structure.
    """
    def __init__(self, data_=None, charset: str = "utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK,
                 **kwargs):
        content_type = "application/json"
        if charset:
            content_type += "; charset="+charset
        if kwargs and data_ is not None:
            raise RuntimeError("Only one of data and kwargs is allowed.")
        if kwargs and data_ is None:
            data_ = kwargs
        super().__init__(dumps(data_), content_type, headers, status_code)


class TextResponse(Response):
    """Simple text/plain response."""
    def __init__(self, text: str, charset: str = "utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        content_type = "text/plain"
        if charset:
            content_type += "; charset="+charset

        super().__init__(text, content_type, headers, status_code)


class FileObjResponse(BaseResponse):
    """FileResponse returns file object direct to WSGI server.

    This means, that sendfile UNIX system call can be used.

    Be careful not to use a single FileReponse instance multiple times!
    WSGI server closes file, which is returned by this response. So just
    like Response, instance of FileResponse can be used only once!

    File content is returned from current position. So Content-Length is set
    from file system or from buffer, but minus position.
    """
    def __init__(self, file_obj: Union[IOBase, BinaryIO],
                 content_type: Optional[str] = None,
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        assert file_obj.readable()
        assert not isinstance(file_obj, TextIOBase), \
            "file_obj must be binary stream"
        if content_type is None:     # default mime type
            content_type = "application/octet-stream"
        super().__init__(content_type=content_type,
                         headers=headers,
                         status_code=status_code)
        self.__file = file_obj
        if file_obj.seekable():
            self.__pos = file_obj.tell()
        try:
            self.__content_length = \
                    fstat(file_obj.fileno()).st_size - self.__pos
        except OSError:
            if isinstance(file_obj, BytesIO):
                self.__content_length = \
                        file_obj.getbuffer().nbytes - self.__pos
            else:
                self.__content_length = 0
                print(type(file_obj))
                log.debug('File object has unknown size.')

    # must be redefined, because self.__buffer is private attribute
    @property
    def data(self):
        """Return data content.

        This property works only if file_obj is seekable.
        """
        if self.__file.seekable():
            self.__file.seek(self.__pos)
            return self.__file.read()
        log.info('File object is not seekable.')
        return b''

    @property
    def content_length(self):
        """Return content_length of response.

        That is size of internal buffer.
        """
        return self.__content_length

    # must be redefined, because self.__buffer is private attribute
    def __end_of_response__(self):
        """Method **for internal use only!**.

        This method was called from Application object at the end of request
        for returning right value to wsgi server.
        """
        if self.__file.seekable():
            self.__file.seek(self.__pos)
        return self.__file


class FileResponse(FileObjResponse):
    """FileResponse returns opened file direct to WSGI server.

    This means, that sendfile UNIX system call can be used.

    Be careful not to use a single FileReponse instance multiple times!
    WSGI server closes file, which is returned by this response. So just
    like Response, instance of FileResponse can be used only once!

    This object adds Last-Modified header, if is not set.
    """
    def __init__(self, path: str, content_type: Optional[str] = None,
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        if not access(path, R_OK):
            raise IOError("Could not stat file for reading")
        if content_type is None:     # auto mime type select
            # pylint: disable=unused-variable
            (content_type, encoding) = mimetypes.guess_type(path)

        # pylint: disable=consider-using-with
        super().__init__(open(path, 'rb', buffering=0),
                         content_type=content_type,
                         headers=headers,
                         status_code=status_code)

        if 'Last-Modified' not in self.headers:
            self.add_header('Last-Modified', time_to_http(getctime(path)))


class GeneratorResponse(BaseResponse):
    """For response, which use generator as returned value.

    Even though you can figure out iterating your generator more times, just
    like Response, instance of GeneratorResponse can be used only once!
    """
    def __init__(self, generator: Iterable[bytes],
                 content_type: str = "text/html; charset=utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        super().__init__(content_type=content_type,
                         headers=headers,
                         status_code=status_code)
        self.__generator = generator

    def __end_of_response__(self):
        return self.__generator


class StrGeneratorResponse(GeneratorResponse):
    """Generator response where generator returns str."""
    def __init__(self, generator: Iterable[str],
                 content_type: str = "text/html; charset=utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK):
        super().__init__([b''], content_type=content_type, headers=headers,
                         status_code=status_code)
        self.__generator: Iterable[str] = generator

    def __end_of_response__(self):
        return (it.encode("utf-8") for it in self.__generator)


class JSONGeneratorResponse(StrGeneratorResponse):
    """JSON Response for data from generator.

    Data will be processed in generator way, so they need to be buffered.
    This class need simplejson module.

    ** kwargs from constructor are serialized to json structure.
    """
    def __init__(self, charset: str = "utf-8",
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 status_code: int = HTTP_OK,
                 **kwargs):
        if not JSON_GENERATOR:
            # pyl-int: disable=super-init-not-called
            raise NotImplementedError(
                "JSONGeneratorResponse need simplejson module")

        mime_type = "application/json"
        if charset:
            mime_type += "; charset="+charset
        generator = JSONEncoder(  # type: ignore
            iterable_as_array=True).iterencode(kwargs)  # type: ignore
        super().__init__(generator, mime_type, headers, status_code)


class NoContentResponse(BaseResponse):
    """For situation, where only state is returned."""
    def __init__(self,
            headers: Optional[Union[Headers, HeadersList]] = None,
            status_code: int = HTTP_NO_CONTENT):
        super().__init__(headers=headers, status_code=status_code)

    def __start_response__(self, start_response: Callable):
        start_response(
            "%d %s" % (self.status_code, self.reason), [])


class EmptyResponse(NoContentResponse):
    """Compatibility response"""
    @deprecated("use NoContentResponse instead.")
    def __init__(self, status_code: int = HTTP_NO_CONTENT):
        super().__init__(status_code=status_code)


class Declined(NoContentResponse):
    """For situation without answer.

    This response is returned, when state.DECLINED was returned.
    """
    def __init__(self, status_code: int = HTTP_OK):
        super().__init__(status_code=status_code)

    @property
    def headers(self):
        """Declined response don't have headers."""
        return Headers()

    @headers.setter
    def headers(self, value):
        # pylint: disable=unused-argument,logging-format-interpolation
        stack_record = stack()[1]
        log.warning("Declined response don't use headers.\n"
                    "  File {1}, line {2}, in {3} \n"
                    "{0}".format(stack_record[4][0], *stack_record[1:4]))

    def add_header(self, *args, **kwargs):
        """Declined response don't have headers"""
        # pylint: disable=unused-argument,logging-format-interpolation
        stack_record = stack()[1]
        log.warning("Declined response don't use headers.\n"
                    "  File {1}, line {2}, in {3} \n"
                    "{0}".format(stack_record[4][0], *stack_record[1:4]))

    def __call__(self, start_response: Callable):
        log.debug("DECLINED")
        return ()


class RedirectResponse(Response):
    """Redirect the browser to another location.

    A short text is sent to the browser informing that the document has moved
    (for those rare browsers that do not support redirection); this text can
    be overridden by supplying a text string (``message``).

    When ``permanent`` or ``status_code`` is true, MOVED_PERMANENTLY
    status code will be sent to the client, otherwise it will be
    MOVED_TEMPORARILY. **Argument ``permanent`` and ``status_code`` as boolean
    is deprecated. Use real status_code instead.**
    """
    # pylint: disable=too-many-arguments
    def __init__(self, location: str,
                 status_code: Union[int, bool] = HTTP_MOVED_TEMPORARILY,
                 message: Union[str, bytes] = b'',
                 headers: Optional[Union[Headers, HeadersList]] = None,
                 permanent: bool = False):
        if status_code is True or permanent:
            log.warning('Argument `permanent` is deprecated. '
                        ' Use real status_code instead.')
            status_code = HTTP_MOVED_PERMANENTLY
        super().__init__(message,
                         content_type="text/plain",
                         headers=headers,
                         status_code=status_code)
        self.add_header("Location", location)


class NotModifiedResponse(NoContentResponse):
    """Not Modified Response."""
    def __init__(self,
            headers: Optional[Union[Headers, HeadersList]] = None,
            etag: Optional[str] = None,
            content_location: Optional[str] = None,
            date: Optional[Union[str, int, datetime]] = None,
            vary: Optional[str] = None):
        # pylint: disable=too-many-arguments

        super().__init__(status_code=HTTP_NOT_MODIFIED, headers=headers)
        if etag:
            self.add_header('E-Tag', etag)
        if content_location:
            self.add_header('Content-Location', content_location)
        if isinstance(date, str) and date:
            self.add_header('Date', date)
        elif isinstance(date, int):
            self.add_header('Date', time_to_http(date))
        elif isinstance(date, datetime):
            self.add_header('Date', datetime_to_http(date))
        if vary:
            self.add_header('Vary', vary)


class ResponseError(RuntimeError):
    """Exception for bad response values."""


class HTTPException(Exception):
    """HTTP Exception to fast stop work.

    Simple error exception:

    >>> HTTPException(404)  # doctest: +ELLIPSIS
    HTTPException(404, {}...)

    Exception with response:

    >>> HTTPException(Response(data=b'Created', status_code=201))
    ...                     # doctest: +ELLIPSIS
    HTTPException(<poorwsgi.response.Response object at 0x...>...)

    Attributes:

    >>> HTTPException(401, stale=True)  # doctest: +ELLIPSIS
    HTTPException(401, {'stale': True}...)
    """
    def __init__(self, arg: Union[int, Response], **kwargs):
        """status_code is one of HTTP_* status code from state module.

        If response is set, that will use, otherwise the handler from
        Application will be call."""
        assert isinstance(arg, (int, Response))
        super().__init__(arg, kwargs)

    def make_response(self):
        """Return or make a response if is possible."""
        if isinstance(self.args[0], Response):
            return self.args[0]

        status_code = self.args[0]
        if status_code == DECLINED:
            return Declined()   # decline the connection
        if status_code == HTTP_OK:
            return EmptyResponse()
        return None

    @property
    def response(self):
        """Return response if it was set."""
        if isinstance(self.args[0], Response):
            return self.args[0]
        return None


def make_response(data: Union[str, bytes],
                  content_type: str = "text/html; charset=utf-8",
                  headers: Optional[Union[Headers, HeadersList]] = None,
                  status_code: int = HTTP_OK):
    """Create response from values.

    Data could be string, bytes, or bytes returns iterable object like file.
    """
    try:
        if isinstance(data, (str, bytes)):      # "hello world"
            return Response(data, content_type, headers, status_code)

        iter(data)  # try iter data
        return GeneratorResponse(data, content_type, headers, status_code)
    except Exception:  # pylint: disable=broad-except
        log.exception("Error in processing values: %s, %s, %s, %s",
                      type(data), type(content_type), type(headers),
                      type(status_code))

    raise ResponseError(
        "Returned data must by: <bytes|str>, <str>, <Headers|None>, <int>")


def redirect(location: str,
             status_code: Union[int, bool] = HTTP_MOVED_TEMPORARILY,
             message: Union[str, bytes] = b'',
             headers: Optional[Union[Headers, HeadersList]] = None,
             permanent: bool = False):
    """Raise HTTPException with RedirectResponse response.

    See RedirectResponse, with same interface for more information about
    response.
    """
    raise HTTPException(
        RedirectResponse(location, status_code, message, headers, permanent))


def abort(arg: Union[int, Response]):
    """Raise HTTPException with arg.

    Raise simple error exception:

    >>> abort(404)
    Traceback (most recent call last):
    ...
    poorwsgi.response.HTTPException: (404, {})

    Raise exception with response:

    >>> abort(Response(data=b'Created', status_code=201))
    Traceback (most recent call last):
    ...
    poorwsgi.response.HTTPException:
    (<poorwsgi.response.Response object at 0x...>, {})
    """
    raise HTTPException(arg)
