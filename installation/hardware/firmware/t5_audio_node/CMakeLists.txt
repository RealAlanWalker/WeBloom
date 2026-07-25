##
# T5AI-Core microphone acquisition validation application.
#/

if (CONFIG_ENABLE_AUDIO_CODECS STREQUAL "y")
    set(APP_PATH ${CMAKE_CURRENT_LIST_DIR})
    aux_source_directory(${APP_PATH}/src APP_SRCS)

    add_library(${EXAMPLE_LIB})
    target_sources(${EXAMPLE_LIB} PRIVATE ${APP_SRCS})
    target_include_directories(${EXAMPLE_LIB} PRIVATE
        ${TOP_SOURCE_DIR}/platform/T5AI/t5_os/ap/components/bk_cli/include
        ${TOP_SOURCE_DIR}/platform/T5AI/t5_os/build/bk7258/tuya_app/bk7258_ap/config)
else()
    message(FATAL_ERROR "synbloom_audio_node requires ENABLE_AUDIO_CODECS")
endif()
