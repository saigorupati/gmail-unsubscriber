import httplib2
from googleapiclient.errors import HttpError

from unsubscriber.gmail_client import RealGmailClient, is_retryable


def http_error(status, reason=None):
    content = b"{}" if reason is None else (
        '{"error": {"code": %d, "errors": [{"reason": "%s"}]}}' % (status, reason)
    ).encode()
    return HttpError(httplib2.Response({"status": status}), content)


def msg(mid):
    return {"id": mid, "threadId": "t" + mid, "internalDate": "1000",
            "payload": {"headers": [{"name": "From", "value": "a@a.com"}]}}


class FakeService:
    """Mimics service.users().messages().get() and new_batch_http_request()."""

    def __init__(self, script):
        self.script = script  # list of per-execute behaviours: dict id->exception, or Exception for whole batch
        self.executes = 0

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format, metadataHeaders):
        assert format == "metadata"
        return id

    def new_batch_http_request(self, callback):
        svc = self

        class Batch:
            def __init__(self):
                self.ids = []

            def add(self, req, request_id):
                self.ids.append(request_id)

            def execute(self):
                step = svc.script[min(svc.executes, len(svc.script) - 1)]
                svc.executes += 1
                if isinstance(step, Exception):
                    raise step
                for i in self.ids:
                    err = step.get(i)
                    callback(i, None if err else msg(i), err)

        return Batch()


def client(script, sleeps):
    return RealGmailClient(None, service=FakeService(script), sleep=sleeps.append)


def test_rate_limit_403_is_retryable():
    assert is_retryable(http_error(403, "userRateLimitExceeded"))
    assert is_retryable(http_error(403, "rateLimitExceeded"))
    assert is_retryable(http_error(429))
    assert is_retryable(http_error(503))
    assert not is_retryable(http_error(403, "insufficientPermissions"))
    assert not is_retryable(http_error(400))


def test_per_message_rate_limit_is_retried():
    sleeps = []
    c = client([{"b": http_error(403, "userRateLimitExceeded")}, {}], sleeps)
    assert [m.id for m in c.get_metadata(["a", "b"])] == ["a", "b"]


def test_whole_batch_failure_is_retried():
    sleeps = []
    c = client([http_error(503), {}], sleeps)
    assert [m.id for m in c.get_metadata(["a"])] == ["a"]
    assert sleeps and sleeps[0] >= 1


def test_batches_are_paced():
    sleeps = []
    c = client([{}], sleeps)
    c.get_metadata([str(i) for i in range(120)])  # 3 batches of 50
    assert sleeps.count(1.0) >= 2
