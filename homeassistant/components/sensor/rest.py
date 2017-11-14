"""
Support for RESTful API sensors.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.rest/
"""
import json
import logging

import voluptuous as vol
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_PAYLOAD, CONF_NAME, CONF_VALUE_TEMPLATE, CONF_METHOD, CONF_RESOURCE,
    CONF_UNIT_OF_MEASUREMENT, STATE_UNKNOWN, CONF_VERIFY_SSL, CONF_USERNAME,
    CONF_PASSWORD, CONF_AUTHENTICATION, HTTP_BASIC_AUTHENTICATION,
    ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME,
    HTTP_DIGEST_AUTHENTICATION, CONF_HEADERS)
from homeassistant.helpers.entity import (
    Entity, async_generate_entity_id)
import homeassistant.helpers.config_validation as cv
from jsonpath import jsonpath

REQUIREMENTS = ['jsonpath==0.75']

_LOGGER = logging.getLogger(__name__)

ATTR_JSON_PATH = 'json_path'
CONF_SENSORS = 'sensors'

DEFAULT_METHOD = 'GET'
DEFAULT_NAME = 'REST Sensor'
DEFAULT_VERIFY_SSL = True

ENTITY_ID_FORMAT = 'sensor.{}'

METHODS = ['POST', 'GET']

SENSOR_SCHEMA = vol.Schema({
    vol.Required(ATTR_JSON_PATH): cv.string,
    vol.Required(CONF_VALUE_TEMPLATE): cv.template,
    vol.Optional(ATTR_FRIENDLY_NAME): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_RESOURCE): cv.url,
    vol.Optional(CONF_AUTHENTICATION):
        vol.In([HTTP_BASIC_AUTHENTICATION, HTTP_DIGEST_AUTHENTICATION]),
    vol.Optional(CONF_HEADERS): {cv.string: cv.string},
    vol.Optional(CONF_METHOD, default=DEFAULT_METHOD): vol.In(METHODS),
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_PAYLOAD): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
    vol.Optional(CONF_USERNAME): cv.string,
    vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    vol.Optional(CONF_SENSORS): vol.Schema({cv.slug: SENSOR_SCHEMA})
})


def setup_platform(hass, config, add_devices, discovery_info=None):

    """Set up the RESTful sensor."""
    name = config.get(CONF_NAME)
    resource = config.get(CONF_RESOURCE)
    method = config.get(CONF_METHOD)
    payload = config.get(CONF_PAYLOAD)
    verify_ssl = config.get(CONF_VERIFY_SSL)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    headers = config.get(CONF_HEADERS)
    unit = config.get(CONF_UNIT_OF_MEASUREMENT)
    value_template = config.get(CONF_VALUE_TEMPLATE)
    if value_template is not None:
        value_template.hass = hass

    if username and password:
        if config.get(CONF_AUTHENTICATION) == HTTP_DIGEST_AUTHENTICATION:
            auth = HTTPDigestAuth(username, password)
        else:
            auth = HTTPBasicAuth(username, password)
    else:
        auth = None
    rest = RestData(method, resource, auth, headers, payload, verify_ssl)
    rest.update()

    parent_sensor = RestSensor(hass, rest, name, unit, value_template)
    add_devices([parent_sensor], True)

    if config.get(CONF_SENSORS) is not None:
        for device, device_config in config[CONF_SENSORS].items():
            friendly_name = device_config.get(ATTR_FRIENDLY_NAME, device)
            state_template = device_config[CONF_VALUE_TEMPLATE]
            json_path = device_config.get(ATTR_JSON_PATH)
            state_template.hass = hass

            parent_sensor.sensors.append(
                RestTemplateSensor(
                    hass, device, friendly_name, json_path, state_template
                )
            )
        add_devices(parent_sensor.sensors, True)


class RestTemplateSensor(Entity):
    """Representation of a dynamically generated sensor."""

    def __init__(self, hass, device_id, friendly_name, json_path,
                 state_template):
        """Initialize the sensor."""
        self.hass = hass
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, device_id, hass=hass)
        self._name = friendly_name
        self._json_path = json_path
        self._template = state_template
        self._state = None

    @property
    def name(self):
        """Return the name of this sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of this sensor."""
        return self._state

    @property
    def json_path(self):
        """Return the json path string."""
        return self._json_path

    def set_state(self, value):
        """Set the state of this sensor."""
        self._state = value


class RestSensor(Entity):
    """Implementation of a REST sensor."""

    def __init__(self, hass, rest, name, unit_of_measurement, value_template):
        """Initialize the REST sensor."""
        self._sensors = []
        self._hass = hass
        self.rest = rest
        self._name = name
        self._state = STATE_UNKNOWN
        self._unit_of_measurement = unit_of_measurement
        self._value_template = value_template

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit_of_measurement

    @property
    def available(self):
        """Return if the sensor data are available."""
        return self.rest.data is not None

    @property
    def state(self):
        """Return the state of the device."""
        return self._state
    
    @property
    def sensors(self):
        """Return the list of child sensors."""
        return self._sensors

    def update(self):
        """Get the latest data from REST API and update the state."""
        self.rest.update()
        value = self.rest.data

        # _LOGGER.error("Value is %s", value)
        incoming = value
        if value is None:
            value = STATE_UNKNOWN
        elif self._value_template is not None:
            value = self._value_template.render_with_possible_json_value(
                value, STATE_UNKNOWN)
        if self._sensors is not None:
            json_obj = None
            try:
                 json_obj = json.loads(incoming)
            except ValueError:
                pass

            if json_obj is not None:
                for sensor in self._sensors:
                    sensor_val = None
                    try:
                        res = jsonpath(json_obj, sensor.json_path)
                        if res:
                            sensor_val = res[0]
                    except KeyError:
                        pass
                    sensor.set_state(sensor_val)
        self._state = value


class RestData(object):
    """Class for handling the data retrieval."""

    def __init__(self, method, resource, auth, headers, data, verify_ssl):
        """Initialize the data object."""
        self._request = requests.Request(
            method, resource, headers=headers, auth=auth, data=data).prepare()
        self._verify_ssl = verify_ssl
        self.data = None

    def update(self):
        """Get the latest data from REST service with provided method."""
        try:
            with requests.Session() as sess:
                response = sess.send(
                    self._request, timeout=10, verify=self._verify_ssl)

            self.data = response.text
        except requests.exceptions.RequestException:
            _LOGGER.error("Error fetching data: %s", self._request)
            self.data = None
