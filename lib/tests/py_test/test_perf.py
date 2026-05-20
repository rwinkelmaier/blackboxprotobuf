import pytest
import blackboxprotobuf


@pytest.mark.skip()
def test_wide():
    typedef = {"1": {"type": "int"}}

    message = {"1": [1] * 10000000}

    encoded = blackboxprotobuf.encode(message, typedef, encoding="none")
    decoded = blackboxprotobuf.decode(encoded, typedef, encoding="none").message


@pytest.mark.skip()
def test_deep():
    config = blackboxprotobuf.lib.config.Config()

    typedef = {
        "1": {"type": "message", "message_type_name": "test"},
        "2": {"type": "int"},
    }
    config.known_types["test"] = typedef
    target_depth = 100
    message = {}
    last_layer = message

    while target_depth:
        new_layer = {"2": 1}
        last_layer["1"] = new_layer
        last_layer = new_layer

        target_depth -= 1

    encoded = blackboxprotobuf.encode(message, typedef, encoding="none", config=config)
    decoded = blackboxprotobuf.decode(
        encoded, typedef, encoding="none", config=config
    ).message


@pytest.mark.skip()
def test_large_multilayer():
    config = blackboxprotobuf.lib.config.Config()

    typedef = {
        "1": {"type": "message", "message_type_name": "test"},
        "2": {"type": "int"},
    }
    config.known_types["test"] = typedef
    target_depth = 10
    message = {}
    last_layer = message

    while target_depth:
        new_layer = {"2": [1] * 10000}
        last_layer["1"] = [new_layer] * 2
        last_layer = new_layer

        target_depth -= 1

    encoded = blackboxprotobuf.encode(message, typedef, encoding="none", config=config)
    decoded = blackboxprotobuf.decode(
        encoded, typedef, encoding="none", config=config
    ).message


@pytest.mark.skip()
def test_uint_message_perf():
    config = blackboxprotobuf.lib.config.Config()

    typedef = {
        "2": {"type": "int"},
    }

    message = {"2": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}

    import timeit

    time_taken = timeit.timeit(
        lambda: blackboxprotobuf.encode(
            message, typedef, encoding="none", config=config
        ),
        number=100000,
    )
    print("Encoding took {:.4f} seconds".format(time_taken))

    payload = blackboxprotobuf.encode(message, typedef, encoding="none", config=config)
    time_taken = timeit.timeit(
        lambda: blackboxprotobuf.decode(
            payload, typedef, encoding="none", config=config
        ),
        number=100000,
    )
    print("Decoding took {:.4f} seconds".format(time_taken))
