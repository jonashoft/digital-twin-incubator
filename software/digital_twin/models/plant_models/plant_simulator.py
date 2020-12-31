import logging

from influxdb_client.client.write_api import SYNCHRONOUS
from oomodelling import ModelSolver

from communication.server.rpc_server import RPCServer
from communication.shared.connection_parameters import *
from communication.shared.protocol import from_s_to_ns, \
    ROUTING_KEY_PLANTSIMULATOR4
from digital_twin.data_access.dbmanager.data_access_parameters import INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET
from digital_twin.data_access.dbmanager.incubator_data_query import IncubatorDataQuery
from digital_twin.models.plant_models.four_parameters_model.four_parameter_model import FourParameterIncubatorPlant
from digital_twin.models.plant_models.model_functions import create_lookup_table
import numpy as np


class PlantSimulator4Params(RPCServer):
    """
    Can run simulations of the plant.
    """

    def __init__(self, ip=RASPBERRY_IP,
                 port=RASPBERRY_PORT,
                 username=PIKA_USERNAME,
                 password=PIKA_PASSWORD,
                 vhost=PIKA_VHOST,
                 exchange_name=PIKA_EXCHANGE,
                 exchange_type=PIKA_EXCHANGE_TYPE
                 ):
        super().__init__(ip=ip,
                         port=port,
                         username=username,
                         password=password,
                         vhost=vhost,
                         exchange_name=exchange_name,
                         exchange_type=exchange_type)
        self._l = logging.getLogger("PlantSimulator4Params")

    def start_serving(self):
        super(PlantSimulator4Params, self).start_serving(ROUTING_KEY_PLANTSIMULATOR4, ROUTING_KEY_PLANTSIMULATOR4)

    def on_run(self, timespan_seconds,
               C_air,
               G_box,
               C_heater,
               G_heater,
               initial_box_temperature,
               initial_heat_temperature,
               room_temperature,
               heater_on):

        self._l.debug("Ensuring that we have a consistent set of samples.")
        if not (len(room_temperature) == len(heater_on) == len(timespan_seconds)):
            error_msg = f"Inconsistent number of samples found:" \
                        f"len(room_temperature)={len(room_temperature)}" \
                        f"len(heater_on)={len(heater_on)}" \
                        f"len(timespan_seconds)={len(timespan_seconds)}"
            self._l.warning(error_msg)
            return {"error": error_msg}

        self._l.debug("Checking if there are enough samples.")
        if len(timespan_seconds) < 1:
            error_msg = f"Not enough data exists in the period specified by timespan_seconds" \
                        f"Found only {len(timespan_seconds)} samples."
            self._l.warning(error_msg)
            return {"error": error_msg}

        room_temperature_fun = create_lookup_table(timespan_seconds, room_temperature)
        heater_on_fun = create_lookup_table(timespan_seconds, heater_on)

        self._l.debug("Wiring model.")
        model = FourParameterIncubatorPlant(initial_room_temperature=room_temperature[0],
                                            initial_box_temperature=initial_box_temperature,
                                            initial_heat_temperature=initial_heat_temperature,
                                            C_air=C_air, G_box=G_box,
                                            C_heater=C_heater, G_heater=G_heater)
        model.in_room_temperature = lambda: room_temperature_fun(model.time())
        model.in_heater_on = lambda: heater_on_fun(model.time())

        start_t = timespan_seconds[0]
        end_t = timespan_seconds[-1]
        max_step_size = timespan_seconds[1] - timespan_seconds[0]

        self._l.debug(f"Simulating model from time {start_t} to {end_t} with a maximum step size of {max_step_size}, "
                      f"and a total of {len(timespan_seconds)} samples.")
        try:
            sol = ModelSolver().simulate(model, start_t, end_t, max_step_size,
                                         t_eval=timespan_seconds)

            self._l.debug(f"Converting solution to influxdb data format.")
            state_names = model.state_names()
            state_over_time = sol.y
            self._l.debug(f"Solution has {len(state_over_time[0])} samples.")

            def get_signal(state):
                index = np.where(state_names == state)
                assert len(index) == 1
                signal = state_over_time[index[0], :][0]
                return signal.tolist()

            T_solution = get_signal("T")
            T_heater_solution = get_signal("T_heater")

            results = {
                "T":  T_solution,
                "T_heater": T_heater_solution
            }
            self._l.debug(f"Sending results back.")

        except ValueError as error:
            msg = f"Exception while running simulation: {error}."
            self._l.error(msg)
            results = {"error": msg}

        return results
