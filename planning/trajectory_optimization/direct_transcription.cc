#include "drake/planning/trajectory_optimization/direct_transcription.h"

#include <algorithm>
#include <cstddef>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "drake/common/symbolic/decompose.h"
#include "drake/math/autodiff.h"
#include "drake/math/autodiff_gradient.h"
#include "drake/solvers/constraint.h"
#include "drake/systems/analysis/explicit_euler_integrator.h"
#include "drake/systems/framework/system_symbolic_inspector.h"

namespace drake {
namespace planning {
namespace trajectory_optimization {

using systems::Context;
using systems::DiscreteValues;
using systems::ExplicitEulerIntegrator;
using systems::FixedInputPortValue;
using systems::InputPort;
using systems::InputPortIndex;
using systems::InputPortSelection;
using systems::IntegratorBase;
using systems::PeriodicEventData;
using systems::PortDataType;
using systems::System;
using systems::SystemSymbolicInspector;
using systems::TimeVaryingLinearSystem;
using trajectories::PiecewisePolynomial;

namespace {

// Implements a constraint on the defect between the state variables
// advanced for one discrete step or one integration for a fixed time step,
// and the decision variable representing the next state.
class DirectTranscriptionConstraint : public solvers::Constraint {
 public:
  DRAKE_NO_COPY_NO_MOVE_NO_ASSIGN(DirectTranscriptionConstraint);

  // @param system The system describing the dynamics of the constraint.
  // The reference must remain valid for the lifetime of this constraint.
  // @param context A mutable pointer to a context that will be written to in
  // order to perform the dynamics evaluations.  This context must also
  // stay valid for the lifetime of this constraint.
  // @param input_port_value A pre-allocated mutable pointer for writing the
  // input value, which must be assigned as an input to @p context.  It must
  // also remain valid.
  // @param num_states the integer size of the discrete or continuous
  // state vector being optimized.
  // @param num_inputs the integer size of the input vector being optimized.
  // @param evaluation_time  The time along the trajectory at which this
  // constraint is evaluated.
  // @param fixed_time_step Defines the explicit Euler integration
  // time step for systems with continuous state variables.
  DirectTranscriptionConstraint(IntegratorBase<AutoDiffXd>* integrator,
                                FixedInputPortValue* input_port_value,
                                int num_states, int num_inputs,
                                double evaluation_time,
                                TimeStep fixed_time_step)
      : Constraint(num_states, num_inputs + 2 * num_states,
                   Eigen::VectorXd::Zero(num_states),
                   Eigen::VectorXd::Zero(num_states)),
        integrator_(integrator),
        input_port_value_(input_port_value),
        num_states_(num_states),
        num_inputs_(num_inputs),
        evaluation_time_(evaluation_time),
        fixed_time_step_(fixed_time_step.value) {
    DRAKE_DEMAND(evaluation_time >= 0.0);
    const Context<AutoDiffXd>& context = integrator->get_context();
    DRAKE_DEMAND(context.has_only_discrete_state() ||
                 context.has_only_continuous_state());
    DRAKE_DEMAND(context.num_input_ports() == 0 ||
                 input_port_value_ != nullptr);

    if (context.has_only_discrete_state()) {
      discrete_state_ = integrator_->get_system().AllocateDiscreteVariables();
    } else {
      DRAKE_DEMAND(fixed_time_step_ > 0.0);
    }

    // Makes sure the autodiff vector is properly initialized.
    evaluation_time_.derivatives().resize(2 * num_states_ + num_inputs_);
    evaluation_time_.derivatives().setZero();
  }

  ~DirectTranscriptionConstraint() override = default;

 protected:
  void DoEval(const Eigen::Ref<const Eigen::VectorXd>& x,
              Eigen::VectorXd* y) const override {
    AutoDiffVecXd y_t;
    Eval(x.cast<AutoDiffXd>(), &y_t);
    *y = math::ExtractValue(y_t);
  }

  // The format of the input to the eval() function is a vector
  // containing {input, state, next_state}.
  void DoEval(const Eigen::Ref<const AutoDiffVecXd>& x,
              AutoDiffVecXd* y) const override {
    DRAKE_ASSERT(x.size() == num_inputs_ + (2 * num_states_));

    // Extract our input variables:
    const auto input = x.head(num_inputs_);
    const auto state = x.segment(num_inputs_, num_states_);
    const auto next_state = x.tail(num_states_);

    Context<AutoDiffXd>* context = integrator_->get_mutable_context();
    context->SetTime(evaluation_time_);
    if (context->num_input_ports() > 0) {
      input_port_value_->GetMutableVectorData<AutoDiffXd>()->SetFromVector(
          input);
    }

    if (context->has_only_continuous_state()) {
      // Compute the defect between next_state and the explicit Euler
      // integration.
      context->SetContinuousState(state);
      DRAKE_THROW_UNLESS(integrator_->IntegrateWithSingleFixedStepToTime(
          evaluation_time_ + fixed_time_step_));
      *y = next_state - context->get_continuous_state_vector().CopyToVector();
    } else {
      context->SetDiscreteState(0, state);
      discrete_state_->SetFrom(
          integrator_->get_system().EvalUniquePeriodicDiscreteUpdate(*context));
      *y = next_state - discrete_state_->get_vector(0).get_value();
    }
  }

  void DoEval(const Eigen::Ref<const VectorX<symbolic::Variable>>&,
              VectorX<symbolic::Expression>*) const override {
    throw std::logic_error(
        "DirectTranscriptionConstraint does not support symbolic evaluation.");
  }

 private:
  IntegratorBase<AutoDiffXd>* const integrator_;
  std::unique_ptr<DiscreteValues<AutoDiffXd>> discrete_state_;
  FixedInputPortValue* const input_port_value_{nullptr};

  const int num_states_{0};
  const int num_inputs_{0};
  AutoDiffXd evaluation_time_{0};
  const double fixed_time_step_{0};
};

double get_period(const System<double>* system) {
  if (system->num_abstract_states() > 0) {
    throw std::logic_error(
        "DirectTranscription cannot operate on systems with abstract state. "
        "(For a MultibodyPlant, set its use_sampled_output_ports config option "
        "to false to remove the unwanted abstract state.)");
  }
  std::optional<PeriodicEventData> periodic_data =
      system->GetUniquePeriodicDiscreteUpdateAttribute();
  if (!periodic_data.has_value()) {
    throw std::logic_error(
        "This constructor is for discrete-time systems with a single unique "
        "update period. For continuous-time systems, you must use a different "
        "constructor that specifies the time steps.");
  }
  DRAKE_DEMAND(periodic_data->offset_sec() == 0.0);
  return periodic_data->period_sec();
}

int get_input_port_size(
    const System<double>* system,
    std::variant<InputPortSelection, InputPortIndex> input_port_index) {
  DRAKE_THROW_UNLESS(system != nullptr);
  if (system->get_input_port_selection(input_port_index)) {
    return system->get_input_port_selection(input_port_index)->size();
  } else {
    return 0;
  }
}

}  // namespace

DirectTranscription::DirectTranscription(
    const System<double>* system, const Context<double>& context,
    int num_time_samples,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index)
    : MultipleShooting(get_input_port_size(system, input_port_index),
                       context.num_total_states(), num_time_samples,
                       get_period(system)),
      discrete_time_system_(true) {
  ValidateSystem(*system, context, input_port_index);

  // First try symbolic dynamics.
  if (!AddSymbolicDynamicConstraints(system, context, input_port_index)) {
    AddAutodiffDynamicConstraints(system, context, input_port_index);
  }
  ConstrainEqualInputAtFinalTwoTimesteps();
}

DirectTranscription::DirectTranscription(
    const TimeVaryingLinearSystem<double>* system,
    const Context<double>& context, int num_time_samples,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index)
    : MultipleShooting(get_input_port_size(system, input_port_index),
                       context.num_total_states(), num_time_samples,
                       std::max(system->time_period(),
                                std::numeric_limits<double>::epsilon())
                       /* N.B. Ensures that MultipleShooting is well-formed */),
      discrete_time_system_(true) {
  if (!context.has_only_discrete_state()) {
    throw std::invalid_argument(
        "This constructor is for discrete-time systems.  For continuous-time "
        "systems, you must use a different constructor that specifies the "
        "time steps.");
  }
  ValidateSystem(*system, context, input_port_index);

  for (int i = 0; i < N() - 1; i++) {
    const double t = system->time_period() * i;
    prog().AddLinearEqualityConstraint(
        state(i + 1).cast<symbolic::Expression>() ==
        system->A(t) * state(i).cast<symbolic::Expression>() +
            system->B(t) * input(i).cast<symbolic::Expression>());
  }
  ConstrainEqualInputAtFinalTwoTimesteps();
}

DirectTranscription::DirectTranscription(
    const System<double>* system, const Context<double>& context,
    int num_time_samples, TimeStep fixed_time_step,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index)
    : MultipleShooting(get_input_port_size(system, input_port_index),
                       context.num_total_states(), num_time_samples,
                       fixed_time_step.value),
      discrete_time_system_(false) {
  if (!context.has_only_continuous_state()) {
    throw std::invalid_argument(
        "This constructor is for continuous-time systems.  For discrete-time "
        "systems, you must use a different constructor that doesn't specify "
        "the time step.");
  }
  DRAKE_DEMAND(fixed_time_step.value > 0.0);
  if (context.num_input_ports() > 0) {
    DRAKE_DEMAND(num_inputs() == get_input_port_size(system, input_port_index));
  }

  // First try symbolic dynamics.
  if (!AddSymbolicDynamicConstraints(system, context, input_port_index)) {
    AddAutodiffDynamicConstraints(system, context, input_port_index);
  }
  ConstrainEqualInputAtFinalTwoTimesteps();
}

DirectTranscription::~DirectTranscription() {}

void DirectTranscription::DoAddRunningCost(const symbolic::Expression& g) {
  // Cost = \sum_n g(n,x[n],u[n]) dt
  for (int i = 0; i < N() - 1; i++) {
    prog().AddCost(SubstitutePlaceholderVariables(g * fixed_time_step(), i));
  }
}

PiecewisePolynomial<double> DirectTranscription::ReconstructInputTrajectory(
    const solvers::MathematicalProgramResult& result) const {
  Eigen::VectorXd times = GetSampleTimes(result);
  std::vector<double> times_vec(N());
  std::vector<Eigen::MatrixXd> inputs(N());

  for (int i = 0; i < N(); i++) {
    times_vec[i] = times(i);
    inputs[i] = result.GetSolution(input(i));
  }
  // TODO(russt): Implement DTTrajectories and return one of those instead.
  return PiecewisePolynomial<double>::ZeroOrderHold(times_vec, inputs);
}

PiecewisePolynomial<double> DirectTranscription::ReconstructStateTrajectory(
    const solvers::MathematicalProgramResult& result) const {
  Eigen::VectorXd times = GetSampleTimes(result);
  std::vector<double> times_vec(N());
  std::vector<Eigen::MatrixXd> states(N());

  for (int i = 0; i < N(); i++) {
    times_vec[i] = times(i);
    states[i] = result.GetSolution(state(i));
  }
  // TODO(russt): Implement DTTrajectories and return one of those instead.
  // TODO(russt): For continuous time, this should return a cubic polynomial.
  return PiecewisePolynomial<double>::ZeroOrderHold(times_vec, states);
}

bool DirectTranscription::AddSymbolicDynamicConstraints(
    const System<double>* system, const Context<double>& context,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index) {
  using symbolic::Expression;
  const auto symbolic_system = system->ToSymbolicMaybe();
  if (!symbolic_system) {
    return false;
  }
  auto symbolic_context = symbolic_system->CreateDefaultContext();
  if (SystemSymbolicInspector::IsAbstract(*symbolic_system,
                                          *symbolic_context)) {
    return false;
  }
  symbolic_context->SetTimeStateAndParametersFrom(context);

  const InputPort<Expression>* input_port =
      symbolic_system->get_input_port_selection(input_port_index);

  ExplicitEulerIntegrator<Expression> integrator(
      *symbolic_system, fixed_time_step(), symbolic_context.get());
  integrator.Initialize();
  VectorX<Expression> next_state(num_states());

  for (int i = 0; i < N() - 1; i++) {
    symbolic_context->SetTime(i * fixed_time_step());

    if (input_port) {
      input_port->FixValue(symbolic_context.get(), input(i).cast<Expression>());
    }

    if (discrete_time_system_) {
      symbolic_context->SetDiscreteState(state(i).cast<Expression>());
      const DiscreteValues<Expression>& discrete_state =
          symbolic_system->EvalUniquePeriodicDiscreteUpdate(*symbolic_context);
      next_state = discrete_state.get_vector(0).get_value();
    } else {
      symbolic_context->SetContinuousState(state(i).cast<Expression>());
      DRAKE_THROW_UNLESS(integrator.IntegrateWithSingleFixedStepToTime(
          (i + 1) * fixed_time_step()));
      next_state =
          symbolic_context->get_continuous_state_vector().CopyToVector();
    }
    if (i == 0 && !IsAffine(next_state,
                            symbolic::Variables(prog().decision_variables()))) {
      // Note: only check on the first iteration, where we can return false
      // before adding any constraints to the program.  For i>0, the
      // AddLinearEqualityConstraint call with throw.
      return false;
    }
    prog().AddLinearEqualityConstraint(state(i + 1) == next_state);
  }
  return true;
}

void DirectTranscription::AddAutodiffDynamicConstraints(
    const System<double>* system, const Context<double>& context,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index) {
  system_ = system->ToAutoDiffXd();
  DRAKE_DEMAND(system_ != nullptr);
  context_ = system_->CreateDefaultContext();
  input_port_ = system_->get_input_port_selection(input_port_index);

  context_->SetTimeStateAndParametersFrom(context);

  if (input_port_) {
    // Verify that the input port is not abstract valued.
    if (input_port_->get_data_type() == PortDataType::kAbstractValued) {
      throw std::logic_error(
          "The specified input port is abstract-valued, but "
          "DirectTranscription only supports vector-valued input ports.  Did "
          "you perhaps forget to pass a non-default `input_port_index` "
          "argument?");
    }

    // Provide a fixed value for the input port and keep an alias around.
    input_port_value_ = &input_port_->FixValue(
        context_.get(),
        system_->AllocateInputVector(*input_port_)->get_value());
  }

  integrator_ = std::make_unique<ExplicitEulerIntegrator<AutoDiffXd>>(
      *system_, fixed_time_step(), context_.get());
  integrator_->Initialize();

  // For N-1 time steps, add a constraint which depends on the breakpoint
  // along with the state and input vectors at that breakpoint and the
  // next.
  for (int i = 0; i < N() - 1; i++) {
    // Add the dynamic constraints.
    // Note that these constraints all share a context and inout_port_value,
    // so should not be evaluated in parallel.
    auto constraint = std::make_shared<DirectTranscriptionConstraint>(
        integrator_.get(), input_port_value_, num_states(), num_inputs(),
        i * fixed_time_step(), TimeStep{fixed_time_step()});

    prog().AddConstraint(constraint, {input(i), state(i), state(i + 1)});
  }
}

void DirectTranscription::ConstrainEqualInputAtFinalTwoTimesteps() {
  if (num_inputs() > 0) {
    prog().AddLinearEqualityConstraint(input(N() - 2) == input(N() - 1));
  }
}

void DirectTranscription::ValidateSystem(
    const System<double>& system, const Context<double>& context,
    const std::variant<InputPortSelection, InputPortIndex>& input_port_index) {
  DRAKE_DEMAND(system.IsDifferenceEquationSystem());
  DRAKE_DEMAND(num_states() == context.get_discrete_state(0).size());
  if (context.num_input_ports() > 0) {
    DRAKE_DEMAND(num_inputs() ==
                 get_input_port_size(&system, input_port_index));
  }
}

}  // namespace trajectory_optimization
}  // namespace planning
}  // namespace drake
