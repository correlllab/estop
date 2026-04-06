from setuptools import setup

package_name = "estop"

setup(
    name=package_name,
    version="0.0.1",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/estop"]),
        ("share/estop", ["package.xml"]),
        ("share/estop/launch", ["launch/estop.launch.py"]),
    ],
    install_requires=["setuptools", "pyserial"],
    zip_safe=True,
    maintainer="humanoid",
    maintainer_email="humanoid@example.com",
    description="E-stop serial bridge for ROS 2 and optional raw CycloneDDS.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "estop_node = estop.estop_node:main",
            "estop_raw_dds_subscriber = estop.raw_dds_subscriber:main",
            "estop_serial_status_test = estop.serial_status_test:main",
        ],
    },
)
