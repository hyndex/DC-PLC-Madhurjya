cmake_minimum_required(VERSION 3.16)
project(joulepoint_everest_modules)

find_package(everest-core REQUIRED)
include(ev-project-bootstrap)

# Register this repository (types, interfaces, modules)
ev_add_project(${CMAKE_SOURCE_DIR} joulepoint)

# Install out-of-tree modules into everest installation
ev_install_project()

