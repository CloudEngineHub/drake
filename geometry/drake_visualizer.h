#pragma once

#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "drake/common/drake_copyable.h"
#include "drake/geometry/drake_visualizer_params.h"
#include "drake/geometry/geometry_roles.h"
#include "drake/geometry/geometry_version.h"
#include "drake/geometry/query_object.h"
#include "drake/lcm/drake_lcm_interface.h"
#include "drake/systems/framework/diagram_builder.h"
#include "drake/systems/framework/event_status.h"
#include "drake/systems/framework/input_port.h"
#include "drake/systems/framework/leaf_system.h"
#include "drake/systems/framework/output_port.h"

namespace drake {
namespace geometry {
// Forward declare a tester class used as a friend of DrakeVisualizer.
template <typename T>
class DrakeVisualizerTest;

namespace internal {

/* Data stored in the cache; populated when we transmit a load message and
 read from for a pose message.  */
struct DynamicFrameData {
  FrameId frame_id;
  int num_geometry{};
  std::string name;
};

/* If requested in @p params, adds a suffix to the provided LCM channel name,
 based on the geometry role. If a suffix is requested, the passed role
 parameter cannot be kUnassigned. See also DrakeVisualizerParams. */
std::string MakeLcmChannelNameForRole(const std::string& channel,
                                      const DrakeVisualizerParams& params);

}  // namespace internal

// Disable formatter to preserve doxygen tables.
// clang-format off
/** A system that publishes LCM messages representing the current state of a
 SceneGraph instance (whose QueryObject-valued output port is connected to this
 system's input port).

 The messages are compatible with
 <a href="/pydrake/pydrake.visualization.meldis.html">Meldis</a>.

 @system
 name: DrakeVisualizer
 input_ports:
 - query_object
 @endsystem

 The %DrakeVisualizer system broadcasts three kinds of LCM messages:

   - a message that defines the non-deformable geometries in the world on the
     lcm channel named "DRAKE_VIEWER_LOAD_ROBOT"
   - a message that updates the poses of those non-deformable geometries on the
     lcm channel named "DRAKE_VIEWER_DRAW",
   - a message that sets the world space vertex positions of the deformable
     geometries on the lcm channel named "DRAKE_VIEWER_DEFORMABLE"

 If requested in DrakeVisualizerParams, the above channel names are modified
 according to the role specified. This allows simultaneous availability of
 geometry from multiple roles, by using multiple DrakeVisualizer instances.

   - kIllustration: channel names gain a "_ILLUSTRATION" suffix.
   - kProximity: channel names gain a "_PROXIMITY" suffix.
   - kPerception: channel names gain a "_PERCEPTION" suffix.

 The system uses the versioning mechanism provided by SceneGraph to detect
 changes to the geometry so that a change in SceneGraph's data will propagate
 to the message receiver.

 @anchor drake_visualizer_role_consumer
 <h3>Visualization by Role</h3>

 By default, %DrakeVisualizer visualizes geometries with the illustration role
 (see @ref geometry_roles for more details). It can be configured to visualize
 geometries with other roles (see DrakeVisualizerParams). Only one role can be
 specified.

 The appearance of the geometry in the visualizer is typically defined by the
 geometry's properties for the visualized role.

   - For the visualized role, if a geometry has the ("phong", "diffuse")
     property described in the table below, that value is used.
   - Otherwise, if the geometry *also* has the illustration properties, those
     properties are likewise tested for the ("phong", "diffuse") property. This
     rule only has significance is the visualized role is *not* the illustration
     role.
   - Otherwise, the configured default color will be applied (see
     DrakeVisualizerParams).

 | Group name | Required | Property Name |  Property Type  | Property Description |
 | :--------: | :------: | :-----------: | :-------------: | :------------------- |
 |    phong   | no       | diffuse       |     Rgba        | The rgba value of the object surface. |

 <h4>Appearance of OBJ files for non-deformable geometries</h4>

 Meshes represented by OBJ are special. The OBJ file can reference a material
 file (.mtl). If the mtl file is found by the receiving application, values in
 the .mtl will take precedence over the ("phong", "diffuse") geometry property.

 It's worth emphasizing that these rules permits control over the appearance of
 collision geometry on a per-geometry basis by assigning an explicit Rgba value
 to the ("phong", "diffuse") property in the geometry's ProximityProperties.

 @note If collision geometries are added to SceneGraph by parsing URDF/SDF
 files, they will not have diffuse values. Even if elements were added to the
 specification files, they would not be parsed. They must be added to the
 geometries after parsing.

 <h3>Effective visualization</h3>

 The best visualization is when draw messages have been preceded by a compatible
 load message (i.e., a "coherent" message sequence). While LCM doesn't guarantee
 that messages will be received/processed in the same order as they are
 broadcast, your results will be best if %DrakeVisualizer is allowed to
 broadcast coherent messages. Practices that interfere with that will likely
 produce undesirable results. E.g.,

   - Evaluating a single instance of %DrakeVisualizer across several threads,
     such that the data in the per-thread systems::Context varies.
   - Evaluating multiple instances of %DrakeVisualizer in a single thread that
     share the same lcm::DrakeLcmInterface.

 <h3>Mesh support</h3>

 %DrakeVisualizer is mesh file format agnostic. A Mesh or Convex shape that
 references arbitrary mesh files will simply be packaged in the broadcast LCM
 message. It defers to the message *receiver* on whether that particular file
 format is supported.

 <h3>Scalar support and conversion</h3>

 %DrakeVisualizer is templated on `T` and can be used in a `double`- or
 AutoDiffXd-valued Diagram. However, the diagram can only be converted from one
 scalar type to another if the %DrakeVisualizer *owns* its
 lcm::DrakeLcmInterface instance. Attempts to scalar convert the system
 otherwise will throw an exception.
*/
// clang-format on
template <typename T>
class DrakeVisualizer final : public systems::LeafSystem<T> {
 public:
  DRAKE_NO_COPY_NO_MOVE_NO_ASSIGN(DrakeVisualizer);

  /** Creates an instance of %DrakeVisualizer.

   @param lcm     An optional LCM interface. If none is provided, this system
                  will allocate its own instance. If one is provided it must
                  remain valid for the lifetime of this object.
   @param params  The set of parameters to control this system's behavior.
   @throws std::exception if `params.publish_period <= 0`.
   @throws std::exception if `params.role == Role::kUnassigned`.  */
  DrakeVisualizer(lcm::DrakeLcmInterface* lcm = nullptr,
                  DrakeVisualizerParams params = {});

  /** Scalar-converting copy constructor. See @ref system_scalar_conversion.
   It should only be used to convert _from_ double _to_ other scalar types.
   @throws std::exception if `other` does not *own* its lcm::DrakeLcmInterface.
   */
  template <typename U>
  explicit DrakeVisualizer(const DrakeVisualizer<U>& other);

  ~DrakeVisualizer() final;

  /** Returns the QueryObject-valued input port. It should be connected to
   SceneGraph's QueryObject-valued output port. Failure to do so will cause a
   runtime error when attempting to broadcast messages.  */
  const systems::InputPort<T>& query_object_input_port() const {
    return this->get_input_port(query_object_input_port_);
  }

  /** @name Utility functions for instantiating and connecting a visualizer

   These methods provide a convenient mechanism for adding a DrakeVisualizer
   instance to an existing diagram, handling the necessary connections. The
   DrakeVisualizer instance must be connected to a QueryObject-valued output
   port. The difference between the two methods is how that output port is
   identified. Otherwise, the two methods have the same parameters and results.

   Both methods can be invoked with optional parameters:

     - `lcm`: The DrakeVisualizer will use the lcm object provided, otherwise,
        if omitted, the DrakeVisualizer instance will create its own
        self-configured lcm::DrakeLcmInterface object.
     - `params`: The DrakeVisualizer will be configured according to the
        provided parameters. If omitted, it uses default parameters.  */
  //@{

  /** Connects the newly added DrakeVisualizer to the given SceneGraph's
   QueryObject-valued output port.
   The %DrakeVisualizer's name (see systems::SystemBase::set_name) will be set
   to a sensible default value, unless the default name was already in use by
   another system. */
  static const DrakeVisualizer<T>& AddToBuilder(
      systems::DiagramBuilder<T>* builder, const SceneGraph<T>& scene_graph,
      lcm::DrakeLcmInterface* lcm = nullptr, DrakeVisualizerParams params = {});

  /** Connects the newly added DrakeVisualizer to the given QueryObject-valued
   output port.
   The %DrakeVisualizer's name (see systems::SystemBase::set_name) will be set
   to a sensible default value, unless the default name was already in use by
   another system. */
  static const DrakeVisualizer<T>& AddToBuilder(
      systems::DiagramBuilder<T>* builder,
      const systems::OutputPort<T>& query_object_port,
      lcm::DrakeLcmInterface* lcm = nullptr, DrakeVisualizerParams params = {});
  //@}

  // TODO(#7820) When we can easily bind lcmt_* messages, then replace
  //  the DispatchLoadMessage API with something like:
  //  lcmt_load_robot CreateLoadMessage(...)
  //  (etc., for load from context, and draw from context).

  /** (Advanced) Dispatches a load message built on the *model* geometry for the
   given SceneGraph instance. This should be used sparingly. When we have a
   starightforward method for binding lcmtypes in python, this will be replaced
   with an API that will simply generate the lcm *messages* that the caller
   can then do whatever they like with.
   @pre `lcm != nullptr`.  */
  static void DispatchLoadMessage(const SceneGraph<T>& scene_graph,
                                  lcm::DrakeLcmInterface* lcm,
                                  DrakeVisualizerParams params = {});

 private:
  friend class DrakeVisualizerTester;

  /* DrakeVisualizer of different scalar types can all access each other's data.
   */
  template <typename>
  friend class DrakeVisualizer;

  /* Special constructor that optionally leaves the lcm interface unspecified.
   For use of the scalar-converting copy constructor. */
  DrakeVisualizer(lcm::DrakeLcmInterface* lcm, DrakeVisualizerParams params,
                  bool use_lcm);

  /* The periodic event handler. It tests to see if the last scene description
   is valid (if not, updates it) and then broadcasts poses.  */
  systems::EventStatus SendGeometryMessage(
      const systems::Context<T>& context) const;

  /* Dispatches a "load geometry" message (see lcmt_viewer_load_robot) -- the
   declaration of all non-deformable geometries in arbitrary poses and their
   visualizable properties. */
  static void SendLoadNonDeformableMessage(
      const SceneGraphInspector<T>& inspector,
      const DrakeVisualizerParams& params,
      const std::vector<internal::DynamicFrameData>& dynamic_frames,
      double time, lcm::DrakeLcmInterface* lcm);

  /* Dispatches a "draw geometry" message (see lcmt_viewer_draw) -- the
   definition of the poses of all non-deformable geometries. */
  static void SendDrawNonDeformableMessage(
      const QueryObject<T>& query_object, const DrakeVisualizerParams& params,
      const std::vector<internal::DynamicFrameData>& dynamic_frames,
      double time, lcm::DrakeLcmInterface* lcm);

  /* Dispatches a "deformable geometries" message that defines the topology and
   configuration of all deformable geometries at a given time. */
  static void SendDeformableGeometriesMessage(
      const QueryObject<T>& query_object, const DrakeVisualizerParams& params,
      double time, lcm::DrakeLcmInterface* lcm);

  /* Identifies all of the frames with dynamic data and stores them (with
   additional data) in the given vector `frame_data`.
   @note `frame_data` is cleared before any data is added.
   @note There are no guarantees on the order of the entries in `frame_data`. */
  void CalcDynamicFrameData(
      const systems::Context<T>& context,
      std::vector<internal::DynamicFrameData>* frame_data) const;

  /* Refreshes the cached dynamic frame data.  */
  const std::vector<internal::DynamicFrameData>& RefreshDynamicFrameData(
      const systems::Context<T>& context) const;

  /* Simple wrapper for evaluating the dynamic frame data cache entry.  */
  const std::vector<internal::DynamicFrameData>& EvalDynamicFrameData(
      const systems::Context<T>& context) const;

  /* Generic utility for populating the dynamic frames. Available to the ad hoc
   publishing methods as well as the cache-entry instance method.
   @note `frame_data` is cleared before any data is added.
   @note There are no guarantees on the order of the entries in `frame_data`. */
  static void PopulateDynamicFrameData(
      const SceneGraphInspector<T>& inspector,
      const DrakeVisualizerParams& params,
      std::vector<internal::DynamicFrameData>* frame_data);

  typename systems::LeafSystem<T>::GraphvizFragment DoGetGraphvizFragment(
      const typename systems::LeafSystem<T>::GraphvizFragmentParams& params)
      const final;

  /* DrakeVisualizer stores a "model" of what it thinks is registered in the
   receiving application. Because that application is not part of the
   Drake state, this model is likewise not part of the Drake state. It is a
   property of the system. This allows arbitrary changes to the context but
   DrakeVisualizer can still make its *best effort* to ensure that
   the remote state is consistent with the messages it is about to send.
   Because of the nature of lcm messages, it cannot make guarantees; lcm
   messages can arrive in a different order than they were broadcast.

   To this end, DrakeVisualizer has the model (GeometryVersion) and a
   mutex that will allow updating that model safely. Beyond that, there are
   no guarantees about order of operations when the publish callback is
   invoked across multiple threads.  */

  /* The version of the geometry that was last loaded (i.e., had a load message
   sent). If the version found on the input port differs from this value, a
   new load message will be sent prior to the "draw" message.  */
  mutable GeometryVersion version_;
  mutable std::mutex mutex_;

  /* The index of this System's QueryObject-valued input port.  */
  int query_object_input_port_{};

  /* The LCM interface: the owned (if such exists) and the active interface
   (whether owned or not). The active interface is mutable because we non-const
   access to the LCM interface in const System methods.  */
  std::unique_ptr<lcm::DrakeLcmInterface> owned_lcm_{};
  mutable lcm::DrakeLcmInterface* lcm_{};

  /* The index of the cache entry that stores the dynamic frame data for
   non-deformable geometries. */
  systems::CacheIndex frame_data_cache_index_{};

  /* The parameters for the visualizer.  */
  DrakeVisualizerParams params_;
};

/** A convenient alias for the DrakeVisualizer class when using the `double`
scalar type. */
using DrakeVisualizerd = DrakeVisualizer<double>;

}  // namespace geometry

// Define the conversion trait to *only* allow double -> AutoDiffXd conversion.
// Symbolic is not supported yet, and AutoDiff -> double doesn't "make sense".
namespace systems {
namespace scalar_conversion {
template <>
struct Traits<geometry::DrakeVisualizer> : public NonSymbolicTraits {};
}  // namespace scalar_conversion
}  // namespace systems

}  // namespace drake

DRAKE_DECLARE_CLASS_TEMPLATE_INSTANTIATIONS_ON_DEFAULT_NONSYMBOLIC_SCALARS(
    class ::drake::geometry::DrakeVisualizer);
