supla_linux_register_extension(
  NAME matter_light
  INIT_FUNCTION initMatterLightExtension
  SOURCES
    ${CMAKE_CURRENT_LIST_DIR}/matter_light.cpp
  INCLUDE_DIRS
    ${SUPLA_LINUX_PORT_DIR}
    ${_SUPLA_ROOT_FROM_LINUX}/src
)
