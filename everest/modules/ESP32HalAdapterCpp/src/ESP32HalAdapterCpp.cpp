// SPDX-License-Identifier: Apache-2.0
#include <everest/logging.hpp>
#include <generated/interfaces/evse_board_support/Implementation.hpp>
#include <generated/interfaces/power_supply_DC/Implementation.hpp>

using Everest::log;

namespace module {

class ESP32HalAdapterCpp;

class BSPImpl : public evse_board_supportImplBase {
public:
    explicit BSPImpl(const Everest::PtrContainer<ESP32HalAdapterCpp>& mod) : mod(mod) {}

    void init() override {}
    void ready() override {
        // Publish conservative capabilities for DC path (AC parts unused)
        types::evse_board_support::HardwareCapabilities caps;
        caps.max_current_A_import = 200.0;
        caps.min_current_A_import = 0.0;
        caps.max_phase_count_import = 1;
        caps.min_phase_count_import = 1;
        caps.max_current_A_export = 0.0;
        caps.min_current_A_export = 0.0;
        caps.max_phase_count_export = 1;
        caps.min_phase_count_export = 1;
        caps.supports_changing_phases_during_charging = false;
        caps.connector_type = types::evse_board_support::HardwareCapabilities::Connector_typeEnum::IEC62196Type2Socket;
        publish_capabilities(caps);
    }

    // Commands
    void handle_enable(bool& value) override {
        log::info("BSP enable {}", value);
        // No-op; CP handled by ESP32 (Python HAL recommended for production)
    }
    void handle_pwm_on(double& value) override {
        log::info("BSP pwm_on duty={} %", value);
    }
    void handle_pwm_off() override {
        log::info("BSP pwm_off");
    }
    void handle_pwm_F() override {
        log::warn("BSP pwm_F (simulated)");
    }
    void handle_allow_power_on(types::evse_board_support::PowerOnOff& value) override {
        log::info("BSP allow_power_on={}, reason={}", value.allow_power_on, value.reason);
    }

private:
    const Everest::PtrContainer<ESP32HalAdapterCpp>& mod;
};

class PSUImpl : public power_supply_DCImplBase {
public:
    explicit PSUImpl(const Everest::PtrContainer<ESP32HalAdapterCpp>& mod) : mod(mod) {}
    void init() override {}
    void ready() override {
        // Publish basic PSU caps
        types::power_supply_DC::Capabilities caps;
        caps.bidirectional = false;
        caps.current_regulation_tolerance_A = 1.0;
        caps.peak_current_ripple_A = 1.0;
        caps.max_export_voltage_V = 920.0;
        caps.min_export_voltage_V = 0.0;
        caps.max_export_current_A = 200.0;
        caps.min_export_current_A = 0.0;
        caps.max_export_power_W = 920.0 * 200.0;
        publish_capabilities(caps);
        // Initial V/I
        types::power_supply_DC::VoltageCurrent vi{0.0, 0.0};
        publish_voltage_current(vi);
        publish_mode(types::power_supply_DC::Mode::Off);
    }

    void handle_setMode(types::power_supply_DC::Mode& mode, types::power_supply_DC::ChargingPhase& /*phase*/) override {
        log::info("PSU setMode {}", static_cast<int>(mode));
        publish_mode(mode);
    }
    void handle_setExportVoltageCurrent(double& voltage, double& current) override {
        log::debug("PSU setExport V={}V I={}A", voltage, current);
        // In a real implementation forward to ESP32 over UART
    }
    void handle_setImportVoltageCurrent(double& /*voltage*/, double& /*current*/) override {
        // Not used (no import)
    }

private:
    const Everest::PtrContainer<ESP32HalAdapterCpp>& mod;
};

class ESP32HalAdapterCpp : public Everest::ModuleBase {
public:
    ESP32HalAdapterCpp() = default;

    void init() override {
        // Create provided interfaces
        p_bsp = std::make_unique<BSPImpl>(this->shared_from_this<ESP32HalAdapterCpp>());
        p_power = std::make_unique<PSUImpl>(this->shared_from_this<ESP32HalAdapterCpp>());
    }

    void ready() override {}

    std::unique_ptr<BSPImpl> p_bsp;
    std::unique_ptr<PSUImpl> p_power;
};

} // namespace module

// Factory boilerplate
extern "C" EV_SHARED_PTR<ModuleBase> create_module() {
    return EV_SHARED_PTR<module::ESP32HalAdapterCpp>(new module::ESP32HalAdapterCpp());
}

