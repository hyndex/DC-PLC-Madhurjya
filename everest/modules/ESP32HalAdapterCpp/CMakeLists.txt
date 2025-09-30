set(MODULE_NAME ESP32HalAdapterCpp)

add_library(${MODULE_NAME} MODULE
    src/${MODULE_NAME}.cpp
)

target_link_libraries(${MODULE_NAME}
    PRIVATE everest::framework
)

install(TARGETS ${MODULE_NAME}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBEXECDIR}/everest/modules/${MODULE_NAME}
)

