import itertools

import reqt


async def test_streams_results_as_they_complete(server):
    urls = [f"{server}/slow?delay=0.4", f"{server}/ok"]
    order = [result.index async for result in reqt.stream(urls)]
    assert order == [1, 0]


async def test_consumes_a_generator_lazily(server):
    produced = 0

    def urls():
        nonlocal produced
        for i in itertools.count():
            produced += 1
            yield f"{server}/echo?i={i}"

    received = 0
    async for _ in reqt.stream(urls(), concurrency=4):
        received += 1
        if received == 10:
            break
    # Only about one batch of workers plus the queue ahead of the consumer is ever produced
    assert produced < 30


async def test_error_while_reading_urls_is_raised(server):
    def urls():
        yield f"{server}/ok"
        raise RuntimeError("bad input")

    try:
        async for _ in reqt.stream(urls()):
            pass
    except RuntimeError as error:
        assert str(error) == "bad input"
    else:
        raise AssertionError("expected the generator's error")
