# ESP RainMaker Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

This is a Home Assistant custom integration for ESP RainMaker devices, allowing you to control and monitor your ESP32-based IoT devices through the ESP RainMaker cloud platform.

## Features

- **Light Control**: Full support for ESP RainMaker light devices with brightness and color control
- **Sensor Monitoring**: Monitor various sensor readings from your ESP RainMaker devices
- **Config Flow**: Easy setup through Home Assistant's UI
- **Cloud Integration**: Connects to ESP RainMaker cloud services for device management

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner and select "Custom repositories"
4. Add `https://github.com/sayondeep/esp-rainmaker-integration` as an Integration
5. Click "Install"
6. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/sayondeep/esp-rainmaker-integration/releases)
2. Extract the contents to your `custom_components/esp-rainmaker/` directory
3. Restart Home Assistant

## Configuration

1. Go to Configuration → Integrations
2. Click "Add Integration"
3. Search for "ESP RainMaker"
4. Follow the setup wizard to configure your ESP RainMaker credentials

## Supported Devices

- ESP RainMaker Light devices (with brightness and color control)
- ESP RainMaker Sensor devices (temperature, humidity, etc.)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

If you encounter any issues, please [open an issue](https://github.com/sayondeep/esp-rainmaker-integration/issues) on GitHub.
