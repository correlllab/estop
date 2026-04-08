#!/usr/bin/env python3
import argparse
import importlib
import sys
import time
from dataclasses import dataclass


def _import_cyclonedds():
    try:
        DomainParticipant = importlib.import_module(
            "cyclonedds.domain"
        ).DomainParticipant
        IdlStruct = importlib.import_module("cyclonedds.idl").IdlStruct
        Subscriber = importlib.import_module("cyclonedds.sub").Subscriber
        DataReader = importlib.import_module("cyclonedds.sub").DataReader
        Topic = importlib.import_module("cyclonedds.topic").Topic
        return DomainParticipant, IdlStruct, Subscriber, DataReader, Topic
    except Exception as exc:
        print(f"CycloneDDS python bindings not available: {exc}", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raw CycloneDDS subscriber for estop status"
    )
    parser.add_argument(
        "--topic", default="h12/estop_status_raw", help="DDS topic name"
    )
    args = parser.parse_args()

    DomainParticipant, IdlStruct, Subscriber, DataReader, Topic = _import_cyclonedds()

    @dataclass
    class EstopStatusRaw(IdlStruct, typename="h12.estop.EstopStatusRaw"):
        triggered: bool
        plugged_in: bool
        sequence_id: int
        stamp_ms: int

    participant = DomainParticipant()
    subscriber = Subscriber(participant)
    topic = Topic(participant, args.topic, EstopStatusRaw)
    reader = DataReader(subscriber, topic)

    print(f"Listening on raw DDS topic: {args.topic}")
    last_sample_time = None
    while True:
        try:
            samples = reader.take()
            if not samples:
                continue

            for sample in samples:
                now = time.time()
                hz = (
                    0.0
                    if last_sample_time is None
                    else 1.0 / max(now - last_sample_time, 1e-9)
                )
                last_sample_time = now
                print(
                    f"topic={{seq={sample.sequence_id} triggered={sample.triggered} "
                    f"plugged_in={sample.plugged_in} stamp_ms={sample.stamp_ms}}} "
                    f"hz={hz:.2f}"
                )
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
