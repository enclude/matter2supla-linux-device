supla_linux_register_extension(
  NAME matter_meter
  INIT_FUNCTION initMatterMeterExtension
  SOURCES
    ${CMAKE_CURRENT_LIST_DIR}/matter_meter.cpp
  INCLUDE_DIRS
    ${SUPLA_LINUX_PORT_DIR}
    ${_SUPLA_ROOT_FROM_LINUX}/src
)
