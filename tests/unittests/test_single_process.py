import random
import signal
import socket
import time
from threading import Thread
from unittest import mock

from cloudinit import socket as ci_socket


class Sync:
    """A device to send and receive synchronization messages

    Creating an instance of the device sends a b"start"
    """

    def __init__(self, name: str, path: str):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.connect(f"{path}/share/{name}.sock")
        self.sock.bind(f"{path}/share/{name}-return.sock")
        self.sock.sendall(b"start")

    def receive(self):
        """receive 5 bytes from the socket"""
        received = self.sock.recv(5)
        self.sock.close()
        return received


class Timeout:
    """A utility which may be used to verify that a timeout occurs

    TimeoutError is raised on successful timeout.

    Create a signal handler and use signal.alarm to verify that the
    timeout occured.
    """

    def handle_timeout(self, *_):
        raise TimeoutError()

    def __enter__(self):
        signal.signal(signal.SIGALRM, self.handle_timeout)
        # 1 second is, unfortunately, the minimum
        signal.alarm(1)

    def __exit__(self, *_):
        signal.alarm(0)


def test_single_process_times_out(tmp_path):
    """Verify that no "start" makes the protocol block"""
    with mock.patch.object(
        ci_socket, "DEFAULT_RUN_DIR", tmp_path
    ), mock.patch.object(ci_socket, "sd_notify"):
        sync = ci_socket.SocketSync("first")

        try:
            with Timeout():
                # this should block for 1 second
                with sync("first"):
                    pass
        except TimeoutError:
            # success is a timeout
            pass
        else:
            raise AssertionError("Expected the thing to timeout!")


def test_single_process(tmp_path):
    """Verify that a socket can store "start" messages

    After a socket has been been bound but before it has started listening
    """
    expected = b"done"
    with mock.patch.object(
        ci_socket, "DEFAULT_RUN_DIR", tmp_path
    ), mock.patch.object(ci_socket, "sd_notify"):
        sync = ci_socket.SocketSync("first", "second", "third")

        # send all three syncs to the sockets
        first = Sync("first", tmp_path)
        second = Sync("second", tmp_path)
        third = Sync("third", tmp_path)

        # "wait" on the first sync event
        with sync("first"):
            pass

        # check that the first sync returned
        assert expected == first.receive()
        # "wait" on the second sync event
        with sync("second"):
            pass
        # check that the second sync returned
        assert expected == second.receive()
        # "wait" on the third sync event
        with sync("third"):
            pass
        # check that the third sync returned
        assert expected == third.receive()


def test_single_process_threaded(tmp_path):
    """Verify that arbitrary "start" order works"""

    # in milliseconds
    max_sleep = 100
    # initialize random number generator
    random.seed(time.time())
    expected = b"done"
    sync_storage = {}

    def syncer(index: int, name: str):
        """sleep for 0-100ms then send a sync notification

        this allows sync order to be arbitrary
        """
        time.sleep(0.001 * random.randint(0, max_sleep))
        sync_storage[index] = Sync(name, tmp_path)

    with mock.patch.object(
        ci_socket, "DEFAULT_RUN_DIR", tmp_path
    ), mock.patch.object(ci_socket, "sd_notify"):

        sync = ci_socket.SocketSync(
            "first", "second", "third", "fourth", "fifth"
        )

        for i, name in {
            1: "first",
            2: "second",
            3: "third",
            4: "fourth",
            5: "fifth",
        }.items():
            t = Thread(target=syncer, args=(i, name))
            t.run()

        # wait on the first sync event
        with sync("first"):
            pass

        # check that the first sync returned
        assert expected == sync_storage[1].receive()

        # wait on the second sync event
        with sync("second"):
            pass

        # check that the second sync returned
        assert expected == sync_storage[2].receive()

        # wait on the third sync event
        with sync("third"):
            pass

        # check that the third sync returned
        assert expected == sync_storage[3].receive()
        with sync("fourth"):
            pass

        # check that the fourth sync returned
        assert expected == sync_storage[4].receive()

        with sync("fifth"):
            pass

        # check that the fifth sync returned
        assert expected == sync_storage[5].receive()
