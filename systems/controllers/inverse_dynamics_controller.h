#pragma once

#include <memory>
#include <stdexcept>
#include <string>

#include "drake/common/default_scalars.h"
#include "drake/common/drake_copyable.h"
#include "drake/multibody/plant/multibody_plant.h"
#include "drake/systems/controllers/inverse_dynamics.h"
#include "drake/systems/controllers/pid_controller.h"
#include "drake/systems/controllers/state_feedback_controller_interface.h"
#include "drake/systems/framework/diagram.h"

namespace drake {
namespace systems {
namespace controllers {

// N.B. Inheritance order must remain fixed for pydrake (#9243).
/**
 * A state feedback controller that uses a PidController to generate desired
 * accelerations, which are then converted into MultibodyPlant actuation inputs
 * using InverseDynamics (with `mode =` InverseDynamics::kInverseDynamics).
 * More specifically, the output of this controller is:
 * <pre>
 *   actuation = B⁻¹ generalized_force, and
 *   generalized_force = inverse_dynamics(q, v, vd_command), where
 *   vd_command = kp(q_d - q) + kd(v_d - v) + ki int(q_d - q) + vd_d.
 * </pre>
 * Here `q` and `v` stand for the generalized position and velocity, and `vd`
 * is the generalized acceleration, and `B` is the actuation matrix. The
 * subscript `_d` indicates desired values, and `vd_command` indicates the
 * acceleration command (which includes the stabilization terms) passed to the
 * inverse dynamics computation.
 *
 * @system
 * name: InverseDynamicsController
 * input_ports:
 * - estimated_state
 * - desired_state
 * - <span style="color:gray">desired_acceleration</span>
 * output_ports:
 * - actuation
 * - generalized_force
 * @endsystem
 *
 * The desired acceleration port shown in <span style="color:gray">gray</span>
 * may be absent, depending on the arguments passed to the constructor.
 *
 * Note that this class assumes the robot is fully actuated, its position and
 * velocity have the same dimension, and it does not have a floating base. If
 * violated, the program will abort. This controller was not designed for use
 * with a constrained plant (e.g.
 * multibody::MultibodyPlant::num_constraints() > 0): the controller does not
 * account for any constraint forces. Use on such systems is not recommended.
 *
 * @see InverseDynamics for an accounting of all forces incorporated into the
 *      inverse dynamics computation.
 *
 * @tparam_default_scalar
 * @ingroup control_systems
 */
template <typename T>
class InverseDynamicsController final
    : public Diagram<T>,
      public StateFeedbackControllerInterface<T> {
 public:
  DRAKE_NO_COPY_NO_MOVE_NO_ASSIGN(InverseDynamicsController);

  /**
   * Constructs an inverse dynamics controller for the given `plant` model.
   * The %InverseDynamicsController holds an internal, non-owned reference to
   * the MultibodyPlant object so you must ensure that `plant` has a longer
   * lifetime than `this` %InverseDynamicsController.
   * @param plant The model of the plant for control.
   * @param kp Position gain.
   * @param ki Integral gain.
   * @param kd Velocity gain.
   * @param has_reference_acceleration If true, there is an extra BasicVector
   * input port for `vd_d`. If false, `vd_d` is treated as zero, and no extra
   * input port is declared.
   * @param plant_context The context of the `plant` that can be used to
   * override the plant's default parameters. Note that this will be copied at
   * time of construction, so there are no lifetime constraints.
   * @pre `plant` has been finalized (plant.is_finalized() returns `true`).
   * Also, `plant` and `plant_context` must be compatible.
   * @throws std::exception if
   *  - The plant is not finalized (see MultibodyPlant::Finalize()).
   *  - The plant is not compatible with the plant context.
   *  - The number of generalized velocities is not equal to the number of
   *    generalized positions.
   *  - The model is not fully actuated.
   *  - Vector kp, ki and kd do not all have the same size equal to the number
   *    of generalized positions.
   */
  InverseDynamicsController(const multibody::MultibodyPlant<T>& plant,
                            const VectorX<double>& kp,
                            const VectorX<double>& ki,
                            const VectorX<double>& kd,
                            bool has_reference_acceleration,
                            const Context<T>* plant_context = nullptr);

  /**
   * Constructs an inverse dynamics controller and takes the ownership of the
   * input `plant`.
   *
   * @exclude_from_pydrake_mkdoc{This overload is not bound.}
   */
  InverseDynamicsController(std::unique_ptr<multibody::MultibodyPlant<T>> plant,
                            const VectorX<double>& kp,
                            const VectorX<double>& ki,
                            const VectorX<double>& kd,
                            bool has_reference_acceleration,
                            const Context<T>* plant_context = nullptr);

  // Scalar-converting copy constructor.  See @ref system_scalar_conversion.
  template <typename U>
  explicit InverseDynamicsController(const InverseDynamicsController<U>& other);

  ~InverseDynamicsController() override;

  /**
   * Sets the integral part of the PidController to @p value.
   * @p value must be a column vector of the appropriate size.
   */
  void set_integral_value(Context<T>* context,
                          const Eigen::Ref<const VectorX<T>>& value) const;

  /**
   * Returns the input port for the reference acceleration.
   */
  const InputPort<T>& get_input_port_desired_acceleration() const {
    DRAKE_THROW_UNLESS(has_reference_acceleration_);
    DRAKE_DEMAND(desired_acceleration_.is_valid());
    return Diagram<T>::get_input_port(desired_acceleration_);
  }

  /**
   * Returns the input port for the estimated state.
   */
  const InputPort<T>& get_input_port_estimated_state() const final {
    return this->get_input_port(estimated_state_);
  }

  /**
   * Returns the input port for the desired state.
   */
  const InputPort<T>& get_input_port_desired_state() const final {
    return this->get_input_port(desired_state_);
  }

  /**
   * Returns the output port for computed actuation/control.
   */
  const OutputPort<T>& get_output_port_control() const final {
    return this->get_output_port(actuation_);
  }

  /**
   * Returns the output port for computed generalized_force.
   */
  const OutputPort<T>& get_output_port_generalized_force() const {
    return this->get_output_port(generalized_force_);
  }

  /**
   * Returns a constant pointer to the MultibodyPlant used for control.
   */
  const multibody::MultibodyPlant<T>* get_multibody_plant_for_control() const {
    return multibody_plant_for_control_;
  }

 private:
  void SetUp(std::unique_ptr<multibody::MultibodyPlant<T>> owned_plant,
             const VectorX<double>& kp, const VectorX<double>& ki,
             const VectorX<double>& kd, const Context<T>* plant_context);

  const multibody::MultibodyPlant<T>* multibody_plant_for_control_{nullptr};
  PidController<T>* pid_{nullptr};
  const bool has_reference_acceleration_{false};
  InputPortIndex estimated_state_;
  InputPortIndex desired_state_;
  InputPortIndex desired_acceleration_;
  OutputPortIndex actuation_;
  OutputPortIndex generalized_force_;
};

}  // namespace controllers
}  // namespace systems
}  // namespace drake

DRAKE_DECLARE_CLASS_TEMPLATE_INSTANTIATIONS_ON_DEFAULT_SCALARS(
    class ::drake::systems::controllers::InverseDynamicsController);
