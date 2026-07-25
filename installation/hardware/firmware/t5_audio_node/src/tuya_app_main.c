/**
 * @file tuya_app_main.c
 * @brief Stream button-armed microphone sessions to the host over USB UART.
 */

#include <stdint.h>
#include <string.h>

#include "board_com_api.h"
#include "bk_private/bk_cli.h"
#include "tal_api.h"
#include "tdl_audio_manage.h"
#include "tdl_button_manage.h"
#include "tdl_led_manage.h"
#include "tkl_audio.h"
#include "tkl_output.h"

#define AUDIO_SAMPLE_RATE_HZ 16000U
#define AUDIO_SAMPLE_BITS    16U
#define AUDIO_CHANNELS       1U
#define AUDIO_FRAME_BYTES    640U
#define AUDIO_FRAME_SAMPLES  (AUDIO_FRAME_BYTES / 2U)
#define MAX_RECORDING_MS     (3U * 60U * 1000U)
#define AUDIO_QUEUE_DEPTH    25
#define AUDIO_HEALTH_TIMEOUT_MS 1000U

#define STREAM_MAGIC        0x31414253U
#define STREAM_VERSION      1U
#define STREAM_TYPE_START   1U
#define STREAM_TYPE_DATA    2U
#define STREAM_TYPE_END     3U
#define STREAM_END_HOST     1U
#define STREAM_END_TIMEOUT  2U
#define STREAM_END_DISARMED 3U

typedef struct {
    uint8_t type;
    uint16_t length;
    uint16_t flags;
    uint32_t session_id;
    uint32_t chunk_sequence;
    uint32_t first_sample_sequence;
    uint32_t timestamp_ms;
    uint32_t dropped_frames;
    uint8_t payload[AUDIO_FRAME_BYTES];
} AUDIO_QUEUE_MESSAGE_T;

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t header_size;
    uint32_t session_id;
    uint32_t chunk_sequence;
    uint32_t first_sample_sequence;
    uint32_t timestamp_ms;
    uint16_t payload_length;
    uint16_t flags;
    uint32_t dropped_frames;
} AUDIO_STREAM_HEADER_T;

static QUEUE_HANDLE sg_audio_queue = NULL;
static TDL_LED_HANDLE_T sg_led = NULL;
static volatile bool sg_interaction_enabled = false;
static volatile bool sg_recording = false;
static volatile uint32_t sg_session_id = 0;
static volatile uint32_t sg_chunk_sequence = 0;
static volatile uint32_t sg_sample_sequence = 0;
static volatile uint32_t sg_recording_started_ms = 0;
static volatile uint32_t sg_dropped_frames = 0;
static volatile uint32_t sg_last_audio_frame_ms = 0;
static volatile bool sg_audio_restart_attempted = false;

extern int shell_log_raw_data(const uint8_t *data, uint16_t data_length);

static uint16_t __crc16_ccitt(const uint8_t *data, uint32_t length)
{
    uint16_t crc = 0xFFFFU;
    for (uint32_t index = 0; index < length; ++index) {
        crc ^= (uint16_t)data[index] << 8U;
        for (uint8_t bit = 0; bit < 8U; ++bit) {
            crc = (crc & 0x8000U) ? (uint16_t)((crc << 1U) ^ 0x1021U)
                                  : (uint16_t)(crc << 1U);
        }
    }
    return crc;
}

static void __queue_control_message(uint8_t type, uint16_t flags)
{
    AUDIO_QUEUE_MESSAGE_T message = {
        .type = type,
        .flags = flags,
        .session_id = sg_session_id,
        .chunk_sequence = sg_chunk_sequence,
        .first_sample_sequence = sg_sample_sequence,
        .timestamp_ms = (uint32_t)tal_system_get_millisecond(),
        .dropped_frames = sg_dropped_frames,
    };
    if (tal_queue_post(sg_audio_queue, &message, 0) != OPRT_OK) {
        ++sg_dropped_frames;
    }
}

static void __set_led(void)
{
    OPERATE_RET rt = OPRT_OK;
    if (sg_led == NULL) {
        return;
    }
    if (sg_recording) {
        TUYA_CALL_ERR_LOG(tdl_led_flash(sg_led, 200));
    } else {
        TUYA_CALL_ERR_LOG(tdl_led_set_status(
            sg_led, sg_interaction_enabled ? TDL_LED_ON : TDL_LED_OFF));
    }
}

static void __recording_stop(uint16_t reason)
{
    if (!sg_recording) {
        return;
    }
    sg_recording = false;
    __queue_control_message(STREAM_TYPE_END, reason);
    __set_led();
    PR_NOTICE("audio_recording_stop session=%u reason=%u chunks=%u dropped=%u",
              sg_session_id, reason, sg_chunk_sequence, sg_dropped_frames);
}

static void __recording_start(void)
{
    if (!sg_interaction_enabled) {
        PR_NOTICE("audio_recording_rejected reason=interaction_disabled");
        return;
    }
    if (sg_recording) {
        return;
    }
    ++sg_session_id;
    sg_chunk_sequence = 0;
    sg_sample_sequence = 0;
    sg_dropped_frames = 0;
    sg_recording_started_ms = (uint32_t)tal_system_get_millisecond();
    sg_audio_restart_attempted = false;
    sg_recording = true;
    __queue_control_message(STREAM_TYPE_START, 0);
    __set_led();
    PR_NOTICE("audio_recording_start session=%u max_ms=%u", sg_session_id,
              MAX_RECORDING_MS);
}

static void __audio_frame_cb(TDL_AUDIO_FRAME_FORMAT_E type,
                             TDL_AUDIO_STATUS_E status, uint8_t *data,
                             uint32_t length)
{
    if (type != TDL_AUDIO_FRAME_FORMAT_PCM ||
        status != TDL_AUDIO_STATUS_RECEIVING || data == NULL ||
        length != AUDIO_FRAME_BYTES) {
        return;
    }
    const uint32_t now = (uint32_t)tal_system_get_millisecond();
    sg_last_audio_frame_ms = now;
    if (!sg_recording) {
        return;
    }
    if (now - sg_recording_started_ms >= MAX_RECORDING_MS) {
        __recording_stop(STREAM_END_TIMEOUT);
        return;
    }
    AUDIO_QUEUE_MESSAGE_T message = {
        .type = STREAM_TYPE_DATA,
        .length = (uint16_t)length,
        .session_id = sg_session_id,
        .chunk_sequence = sg_chunk_sequence++,
        .first_sample_sequence = sg_sample_sequence,
        .timestamp_ms = now,
        .dropped_frames = sg_dropped_frames,
    };
    sg_sample_sequence += AUDIO_FRAME_SAMPLES;
    memcpy(message.payload, data, length);
    if (tal_queue_post(sg_audio_queue, &message, 0) != OPRT_OK) {
        ++sg_dropped_frames;
    }
}

static void __service_audio_health(void)
{
    if (!sg_recording || sg_chunk_sequence != 0 ||
        sg_audio_restart_attempted) {
        return;
    }
    const uint32_t now = (uint32_t)tal_system_get_millisecond();
    if (now - sg_recording_started_ms < AUDIO_HEALTH_TIMEOUT_MS) {
        return;
    }
    sg_audio_restart_attempted = true;
    PR_WARN("audio_input_stalled last_frame_ms=%u restarting", sg_last_audio_frame_ms);
    const OPERATE_RET stop_result = tkl_ai_stop(0, 0);
    const OPERATE_RET start_result = tkl_ai_start(0, 0);
    if (start_result != OPRT_OK) {
        PR_ERR("audio_input_restart_failed stop=%d start=%d", stop_result,
               start_result);
        __recording_stop(STREAM_END_DISARMED);
        return;
    }
    PR_NOTICE("audio_input_restarted stop=%d", stop_result);
}

static void __button_cb(char *name, TDL_BUTTON_TOUCH_EVENT_E event, void *argc)
{
    (void)name;
    (void)argc;
    if (event != TDL_BUTTON_LONG_PRESS_START) {
        return;
    }
    sg_interaction_enabled = !sg_interaction_enabled;
    if (!sg_interaction_enabled) {
        __recording_stop(STREAM_END_DISARMED);
    }
    __set_led();
    PR_NOTICE("interaction_mode=%s",
              sg_interaction_enabled ? "enabled" : "disabled");
}

static void __set_interaction_enabled(bool enabled)
{
    sg_interaction_enabled = enabled;
    if (!enabled) {
        __recording_stop(STREAM_END_DISARMED);
    }
    __set_led();
    PR_NOTICE("interaction_mode=%s source=host",
              enabled ? "enabled" : "disabled");
}

static void __stream_message(const AUDIO_QUEUE_MESSAGE_T *message)
{
    uint8_t packet[sizeof(AUDIO_STREAM_HEADER_T) + AUDIO_FRAME_BYTES + 2U];
    AUDIO_STREAM_HEADER_T header = {
        .magic = STREAM_MAGIC,
        .version = STREAM_VERSION,
        .type = message->type,
        .header_size = sizeof(AUDIO_STREAM_HEADER_T),
        .session_id = message->session_id,
        .chunk_sequence = message->chunk_sequence,
        .first_sample_sequence = message->first_sample_sequence,
        .timestamp_ms = message->timestamp_ms,
        .payload_length = message->length,
        .flags = message->flags,
        .dropped_frames = message->dropped_frames,
    };
    const uint32_t body_length = sizeof(header) + message->length;
    memcpy(packet, &header, sizeof(header));
    if (message->length > 0) {
        memcpy(packet + sizeof(header), message->payload, message->length);
    }
    const uint16_t crc = __crc16_ccitt(packet, body_length);
    memcpy(packet + body_length, &crc, sizeof(crc));
    shell_log_raw_data(packet, (uint16_t)(body_length + sizeof(crc)));
}

static void __audio_stream_task(void *arg)
{
    (void)arg;
    AUDIO_QUEUE_MESSAGE_T message;
    while (1) {
        if (tal_queue_fetch(sg_audio_queue, &message, 100) == OPRT_OK) {
            __stream_message(&message);
        }
    }
}

static void __cli_audio_start(char *output, int output_length, int argc,
                              char **argv)
{
    (void)output;
    (void)output_length;
    (void)argc;
    (void)argv;
    __recording_start();
}

static void __cli_audio_stop(char *output, int output_length, int argc,
                             char **argv)
{
    (void)output;
    (void)output_length;
    (void)argc;
    (void)argv;
    __recording_stop(STREAM_END_HOST);
}

static void __cli_audio_status(char *output, int output_length, int argc,
                               char **argv)
{
    (void)argc;
    (void)argv;
    snprintf(output, output_length,
             "interaction=%u recording=%u session=%u chunks=%u dropped=%u",
             sg_interaction_enabled, sg_recording, sg_session_id,
              sg_chunk_sequence, sg_dropped_frames);
}

static void __cli_audio_enable(char *output, int output_length, int argc,
                               char **argv)
{
    (void)output;
    (void)output_length;
    (void)argc;
    (void)argv;
    __set_interaction_enabled(true);
}

static void __cli_audio_disable(char *output, int output_length, int argc,
                                char **argv)
{
    (void)output;
    (void)output_length;
    (void)argc;
    (void)argv;
    __set_interaction_enabled(false);
}

static const struct cli_command sg_cli_commands[] = {
    {"audio-start", "start audio session", __cli_audio_start},
    {"audio-stop", "stop audio session", __cli_audio_stop},
    {"audio-status", "show audio status", __cli_audio_status},
    {"audio-enable", "enable interaction mode", __cli_audio_enable},
    {"audio-disable", "disable interaction mode", __cli_audio_disable},
};

static OPERATE_RET __initialize_hardware(void)
{
    OPERATE_RET rt = OPRT_OK;
    TDL_AUDIO_HANDLE_T audio = NULL;
    TDL_AUDIO_INFO_T info = {0};
    TDL_BUTTON_HANDLE button = NULL;
    TDL_BUTTON_CFG_T button_config = {
        .long_start_valid_time = 3000,
        .long_keep_timer = 1000,
        .button_debounce_time = 50,
        .button_repeat_valid_count = 2,
        .button_repeat_valid_time = 0,
    };
    TUYA_CALL_ERR_RETURN(tal_sw_timer_init());
    TUYA_CALL_ERR_RETURN(board_register_hardware());
    TUYA_CALL_ERR_RETURN(tal_queue_create_init(
        &sg_audio_queue, sizeof(AUDIO_QUEUE_MESSAGE_T), AUDIO_QUEUE_DEPTH));
    TUYA_CALL_ERR_RETURN(tdl_audio_find(AUDIO_CODEC_NAME, &audio));
    TUYA_CALL_ERR_RETURN(tdl_audio_open(audio, __audio_frame_cb));
    TUYA_CALL_ERR_RETURN(tdl_audio_get_info(audio, &info));
    if (info.sample_rate != AUDIO_SAMPLE_RATE_HZ ||
        info.sample_bits != AUDIO_SAMPLE_BITS ||
        info.sample_ch_num != AUDIO_CHANNELS ||
        info.frame_size != AUDIO_FRAME_BYTES) {
        return OPRT_INVALID_PARM;
    }
    TUYA_CALL_ERR_RETURN(tdl_button_create(BUTTON_NAME, &button_config, &button));
    tdl_button_event_register(button, TDL_BUTTON_LONG_PRESS_START, __button_cb);
    TUYA_CALL_ERR_RETURN(tdl_button_set_ready_flag(BUTTON_NAME, true));
    sg_led = tdl_led_find_dev(LED_NAME);
    TUYA_CALL_ERR_RETURN(tdl_led_open(sg_led));
    __set_led();
    if (cli_register_commands(sg_cli_commands,
                              sizeof(sg_cli_commands) /
                                  sizeof(sg_cli_commands[0])) != 0) {
        return OPRT_COM_ERROR;
    }
    return OPRT_OK;
}

static void __audio_app_task(void *arg)
{
    (void)arg;
    THREAD_HANDLE stream_thread = NULL;
    THREAD_CFG_T stream_config = {
        .stackDepth = 1024 * 4,
        .priority = THREAD_PRIO_2,
        .thrdname = "audio_stream",
    };
    PR_NOTICE("synbloom_audio_usb_start");
    if (__initialize_hardware() != OPRT_OK) {
        PR_ERR("audio_usb_failed: initialize_hardware");
        return;
    }
    if (tal_thread_create_and_start(&stream_thread, NULL, NULL,
                                    __audio_stream_task, NULL,
                                    &stream_config) != OPRT_OK) {
        PR_ERR("audio_usb_failed: stream_task");
        return;
    }
    PR_NOTICE("audio_usb_ready long_press_ms=3000 max_recording_ms=%u interaction=disabled",
              MAX_RECORDING_MS);
    while (1) {
        __service_audio_health();
        tal_system_sleep(100);
    }
}

void tuya_app_main(void)
{
    THREAD_HANDLE thread = NULL;
    THREAD_CFG_T config = {
        .stackDepth = 1024 * 4,
        .priority = THREAD_PRIO_1,
        .thrdname = "audio_app",
    };
    tal_log_init(TAL_LOG_LEVEL_DEBUG, 1024,
                 (TAL_LOG_OUTPUT_CB)tkl_log_output);
    if (tal_thread_create_and_start(&thread, NULL, NULL, __audio_app_task,
                                    NULL, &config) != OPRT_OK) {
        PR_ERR("audio_usb_failed: app_task");
    }
}
