import copy
from functools import partial
import textwrap
import unittest
import warnings
import weakref

import numpy as np
import scipy.sparse

from pydrake.autodiffutils import AutoDiffXd
from pydrake.common import kDrakeAssertIsArmed, Parallelism
from pydrake.common.test_utilities import numpy_compare
from pydrake.common.test_utilities.deprecation import catch_drake_warnings
from pydrake.common.yaml import yaml_dump_typed, yaml_load_typed
from pydrake.forwarddiff import jacobian
from pydrake.math import ge
from pydrake.solvers import (
    GurobiSolver,
    LinearConstraint,
    MathematicalProgramResult,
    OsqpSolver,
    PyFunctionConstraint,
    ScsSolver,
    SnoptSolver,
    SolverId,
    SolverInterface,
    SolverOptions,
    SolverType,
)
import pydrake.solvers as mp
import pydrake.solvers._testing as mp_testing
import pydrake.symbolic as sym


SNOPT_NO_GUROBI = SnoptSolver().available() and not GurobiSolver().available()
# MathematicalProgram is only bound for float and AutoDiffXd.
SCALAR_TYPES = [float, AutoDiffXd]


class TestQP:
    def __init__(self):
        # Create a simple QP that uses all deduced linear constraint types,
        # along with a quadratic and linear cost.
        # The solution should be [1, 1].
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")
        self.prog = prog
        self.x = x
        self.constraints = [
            # Bounding box
            prog.AddLinearConstraint(x[0] >= 1),
            # Bounding box
            prog.AddLinearConstraint(sym.logical_and(x[1] >= 1, x[1] <= 2.)),
            # Linear inequality
            prog.AddLinearConstraint(3 * x[0] - x[1] <= 2),
            # Linear equality
            prog.AddLinearConstraint(x[0] + 2 * x[1] == 3)]

        # TODO(eric.cousineau): Add constant terms
        self.costs = [prog.AddLinearCost(e=x[0] + x[1]),
                      prog.AddQuadraticCost(0.5 * (x[0]**2 + x[1]**2))]


class TestMathematicalProgram(unittest.TestCase):
    def test_program_construction(self):
        prog = mp.MathematicalProgram()
        vars = prog.NewContinuousVariables(5, "x")
        self.assertEqual(vars.dtype, sym.Variable)
        vars_all = prog.decision_variables()
        self.assertEqual(vars_all.shape, (5,))
        self.assertTrue(prog.IsThreadSafe())

    def test_clone_and_copy_and_deepcopy(self):

        def via_clone(prog):
            return prog.Clone()

        def via_copy(prog):
            return copy.copy(prog)

        def via_deepcopy(prog):
            return copy.deepcopy(prog)

        for copy_method in [via_clone, via_copy, via_deepcopy]:
            with self.subTest(copy_method=copy_method.__name__):
                prog = mp.MathematicalProgram()
                x = prog.NewContinuousVariables(2)
                prog_clone = copy_method(prog)
                self.assertEqual(prog_clone.num_vars(), 2)
                for i in range(2):
                    self.assertEqual(
                        prog_clone.decision_variables()[i].get_id(),
                        x[i].get_id())
                # Add variables to prog, prog_clone should be unchanged.
                prog.NewContinuousVariables(3)
                self.assertEqual(prog_clone.num_vars(), 2)

    def test_program_attributes_and_solver_selection(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")

        # Add linear equality constraints; make sure the solver works.
        prog.AddLinearConstraint(x[0] + x[1] == 0)
        prog.AddLinearConstraint(2*x[0] - x[1] == 1)
        solver_id = mp.ChooseBestSolver(prog)
        self.assertEqual(solver_id.name(), "Linear system")
        solver = mp.MakeSolver(solver_id)
        self.assertEqual(solver.solver_id().name(), "Linear system")
        self.assertTrue(solver.AreProgramAttributesSatisfied(prog))
        self.assertEqual(solver.ExplainUnsatisfiedProgramAttributes(prog), "")
        result = solver.Solve(prog)
        self.assertTrue(result.is_success())
        result = solver.Solve(prog, None, None)
        self.assertTrue(result.is_success())

        # With an inequality constraint added, the "Linear system" solver
        # doesn't work anymore.
        prog.AddLinearConstraint(x[0] >= 0)
        self.assertFalse(solver.AreProgramAttributesSatisfied(prog))
        self.assertTrue(solver.ExplainUnsatisfiedProgramAttributes(prog))
        with self.assertRaises(ValueError):
            solver.Solve(prog, None, None)

        # A different solver will work, though.  We re-use the result object
        # (as a mutable output argument), and make sure that it changes.
        solver_id = mp.ChooseBestSolver(prog)
        self.assertNotEqual(solver_id.name(), "Linear system")
        solver = mp.MakeSolver(solver_id)
        solver.Solve(prog, None, None, result)
        self.assertTrue(result.is_success())
        self.assertEqual(result.get_solver_id().name(), solver_id.name())

        linear_solvers = mp.GetAvailableSolvers(
            prog_type=mp.ProgramType.kLP)
        self.assertGreater(len(linear_solvers), 0)

    def test_module_level_solve_function_and_result_accessors(self):
        qp = TestQP()
        x_expected = np.array([1, 1])
        result = mp.Solve(qp.prog)
        self.assertTrue(result.is_success())
        self.assertTrue(np.allclose(result.get_x_val(), x_expected))
        self.assertEqual(result.get_solution_result(),
                         mp.SolutionResult.kSolutionFound)
        self.assertAlmostEqual(result.get_optimal_cost(), 3.0, places=7)
        self.assertTrue(result.get_solver_id().name())
        self.assertTrue(np.allclose(result.GetSolution(), x_expected))
        self.assertAlmostEqual(result.GetSolution(qp.x[0]), 1.0)
        self.assertTrue(np.allclose(result.GetSolution(qp.x), x_expected))
        self.assertTrue(result.GetSolution(sym.Expression(qp.x[0])).EqualTo(
            result.GetSolution(qp.x[0])))
        m = np.array([sym.Expression(qp.x[0]), sym.Expression(qp.x[1])])
        self.assertTrue(result.GetSolution(m)[1, 0].EqualTo(
            result.GetSolution(qp.x[1])))
        self.assertEqual(result.num_suboptimal_solution(), 0)

        x_val_new = np.array([1, 2])
        result.set_x_val(x_val_new)
        np.testing.assert_array_equal(x_val_new, result.get_x_val())

        result.SetSolution(var=qp.x[0], value=1.5)
        self.assertEqual(result.GetSolution(qp.x[0]), 1.5)

    def test_str(self):
        qp = TestQP()
        s = str(qp.prog)
        self.assertIn("Decision variables", s)
        self.assertIn("LinearConstraint", s)
        self.assertIn("QuadraticCost", s)

    def test_to_latex(self):
        qp = TestQP()
        s = qp.prog.ToLatex(precision=1)
        self.assertIn("\\min", s)
        self.assertIn("\\text{subject to}", s)

# TODO(jwnimmer-tri) MOSEK is also able to solve mixed integer programs;
# perhaps we should test both of them?
    @unittest.skipUnless(GurobiSolver().available(), "Requires Gurobi")
    def test_mixed_integer_optimization(self):
        prog = mp.MathematicalProgram()
        x = prog.NewBinaryVariables(3, "x")
        c = np.array([-1.0, -1.0, -2.0])
        prog.AddLinearCost(c.dot(x))
        a = np.array([1.0, 2.0, 3.0])
        prog.AddLinearConstraint(a.dot(x) <= 4)
        prog.AddLinearConstraint(x[0] + x[1], 1, np.inf)
        prog.AddConstraint(
            LinearConstraint(np.array([[1., 1.]]), np.array([1]),
                             np.array([np.inf])), [x[0], x[1]])
        solver = GurobiSolver()
        result = solver.Solve(prog, None, None)
        self.assertTrue(result.is_success())

        # Test that we got the right solution for all x
        x_expected = np.array([1.0, 0.0, 1.0])
        self.assertTrue(np.all(np.isclose(result.GetSolution(x), x_expected)))

        # Also test by asking for the value of each element of x
        for i in range(3):
            self.assertAlmostEqual(result.GetSolution(x[i]), x_expected[i])

    def test_qp(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")
        # N.B. Scalar-wise logical ops work for Expression, but array ops need
        # the workaround overloads from `pydrake.math`.
        prog.AddLinearConstraint(ge(x, 1))
        prog.AddQuadraticCost(np.eye(2), np.zeros(2), x)
        prog.AddQuadraticCost(np.eye(2), np.zeros(2), 1, x)
        prog.AddQuadraticCost(x.dot(x) + 2)
        prog.AddQuadraticCost(np.eye(2), np.zeros(2), x, is_convex=True)
        prog.AddQuadraticCost(np.eye(2), np.zeros(2), 1, x, is_convex=True)
        prog.AddQuadraticCost(x.dot(x) + 2, is_convex=True)
        # Redundant costs just to check the spelling.
        prog.AddQuadraticErrorCost(vars=x, Q=np.eye(2),
                                   x_desired=np.zeros(2))
        prog.AddQuadraticErrorCost(vars=x, w=1, x_desired=np.ones(2))
        prog.Add2NormSquaredCost(A=np.eye(2), b=np.zeros(2), vars=x)

        result = mp.Solve(prog)
        self.assertTrue(result.is_success())

        x_expected = np.array([1, 1])
        self.assertTrue(np.allclose(result.GetSolution(x), x_expected))

    def test_symbolic_qp(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")
        prog.AddConstraint(x[0], 1., 100.)
        prog.AddConstraint(x[1] >= 1)
        prog.AddQuadraticCost(x[0]**2 + x[1]**2)
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())

        x_expected = np.array([1, 1])
        self.assertTrue(np.allclose(result.GetSolution(x), x_expected))

    def test_bindings(self):
        qp = TestQP()
        self.assertEqual(
            str(qp.constraints[0]),
            "BoundingBoxConstraint\n1 <= x(0) <= inf\n")
        prog = qp.prog
        x = qp.x

        self.assertEqual(prog.FindDecisionVariableIndices(vars=[x[0], x[1]]),
                         [0, 1])
        self.assertEqual(prog.decision_variable_index()[x[0].get_id()], 0)
        self.assertEqual(prog.decision_variable_index()[x[1].get_id()], 1)

        for binding in prog.GetAllCosts():
            self.assertIsInstance(binding.evaluator(), mp.Cost)
        for binding in prog.GetLinearConstraints():
            self.assertIsInstance(binding.evaluator(), mp.Constraint)
        for binding in prog.GetAllConstraints():
            self.assertIsInstance(binding.evaluator(), mp.Constraint)

        self.assertTrue(prog.linear_costs())
        for (i, binding) in enumerate(prog.linear_costs()):
            cost = binding.evaluator()
            self.assertTrue(np.allclose(cost.a(), np.ones((1, 2))))
            self.assertIsNone(cost.gradient_sparsity_pattern())

        self.assertTrue(prog.quadratic_costs())
        for (i, binding) in enumerate(prog.quadratic_costs()):
            cost = binding.evaluator()
            self.assertTrue(np.allclose(cost.Q(), np.eye(2)))
            self.assertTrue(np.allclose(cost.b(), np.zeros(2)))
            self.assertIsNone(cost.gradient_sparsity_pattern())

        self.assertTrue(prog.bounding_box_constraints())
        for (i, binding) in enumerate(prog.bounding_box_constraints()):
            constraint = binding.evaluator()
            self.assertEqual(
                prog.FindDecisionVariableIndex(var=binding.variables()[0]),
                prog.FindDecisionVariableIndex(var=x[i]))
            self.assertIsNone(constraint.gradient_sparsity_pattern())
            num_constraints = constraint.num_constraints()
            if num_constraints == 1:
                self.assertEqual(constraint.GetDenseA(), 1)
                self.assertEqual(constraint.lower_bound(), 1)
                self.assertEqual(constraint.upper_bound(), np.inf)
            else:
                self.assertTrue(np.allclose(constraint.GetDenseA(), np.eye(2)))
                self.assertTrue(np.allclose(constraint.lower_bound(),
                                            [1, -np.inf]))
                self.assertTrue(np.allclose(constraint.upper_bound(),
                                            [np.inf, 2]))

        self.assertTrue(prog.linear_constraints())
        for (i, binding) in enumerate(prog.linear_constraints()):
            constraint = binding.evaluator()
            self.assertIsNone(constraint.gradient_sparsity_pattern())
            self.assertEqual(
                prog.FindDecisionVariableIndex(var=binding.variables()[0]),
                prog.FindDecisionVariableIndex(var=x[0]))
            self.assertEqual(
                prog.FindDecisionVariableIndex(var=binding.variables()[1]),
                prog.FindDecisionVariableIndex(var=x[1]))
            self.assertTrue(np.allclose(constraint.GetDenseA(), [3, -1]))
            self.assertTrue(constraint.lower_bound(), -2)
            self.assertTrue(constraint.upper_bound(), np.inf)

        self.assertTrue(prog.linear_equality_constraints())
        for (i, binding) in enumerate(prog.linear_equality_constraints()):
            self.assertIsNone(constraint.gradient_sparsity_pattern())
            constraint = binding.evaluator()
            self.assertEqual(
                prog.FindDecisionVariableIndex(var=binding.variables()[0]),
                prog.FindDecisionVariableIndex(var=x[0]))
            self.assertEqual(
                prog.FindDecisionVariableIndex(var=binding.variables()[1]),
                prog.FindDecisionVariableIndex(var=x[1]))
            self.assertTrue(np.allclose(constraint.GetDenseA(), [1, 2]))
            self.assertTrue(constraint.lower_bound(), 3)
            self.assertTrue(constraint.upper_bound(), 3)

        result = mp.Solve(prog)
        self.assertTrue(result.is_success())

        x_expected = np.array([1, 1])
        self.assertTrue(np.allclose(result.GetSolution(x), x_expected))

    def test_constraint_api(self):
        prog = mp.MathematicalProgram()
        x0, = prog.NewContinuousVariables(1, "x")
        c = prog.AddLinearConstraint(x0 >= 2).evaluator()
        ce = prog.AddLinearEqualityConstraint(2*x0, 1).evaluator()

        self.assertTrue(c.CheckSatisfied([2.], tol=1e-3))
        satisfied = c.CheckSatisfiedVectorized(
            np.array([1., 2., 3.]).reshape((1, 3)), tol=1e-3)
        self.assertEqual(satisfied, [False, True, True])
        self.assertFalse(c.CheckSatisfied([AutoDiffXd(1.)]))
        self.assertIsInstance(c.CheckSatisfied([x0]), sym.Formula)

        ce.set_description("my favorite constraint")
        self.assertEqual(ce.get_description(), "my favorite constraint")

        def check_bounds(c, A, lb, ub):
            self.assertTrue(np.allclose(c.GetDenseA(), A))
            self.assertTrue(np.allclose(c.lower_bound(), lb))
            self.assertTrue(np.allclose(c.upper_bound(), ub))

        check_bounds(c, [1.], [2.], [np.inf])
        c.UpdateLowerBound([3.])
        check_bounds(c, [1.], [3.], [np.inf])
        c.UpdateUpperBound([4.])
        check_bounds(c, [1.], [3.], [4.])
        c.set_bounds([-10.], [10.])
        check_bounds(c, [1.], [-10.], [10.])
        c.UpdateCoefficients([10.], [-20.], [-30.])
        check_bounds(c, [10.], [-20.], [-30.])

        check_bounds(ce, [2.], [1.], [1.])
        ce.UpdateCoefficients([10.], [20.])
        check_bounds(ce, [10.], [20.], [20.])

    def test_cost_api(self):
        prog = mp.MathematicalProgram()
        x0, = prog.NewContinuousVariables(1, "x")
        lc = prog.AddLinearCost([1], 2, [x0]).evaluator()
        qc = prog.AddQuadraticCost(0.5*x0**2 + 2*x0 + 3).evaluator()

        def check_linear_cost(cost, a, b):
            self.assertTrue(np.allclose(cost.a(), a))
            self.assertTrue(np.allclose(cost.b(), b))

        check_linear_cost(lc, [1.], 2.)
        lc.UpdateCoefficients([10.])
        check_linear_cost(lc, [10.], 0.)

        lc2 = prog.AddLinearCost([2], [x0]).evaluator()
        check_linear_cost(lc2, [2], 0.)

        def check_quadratic_cost(cost, Q, b, c):
            self.assertTrue(np.allclose(cost.Q(), Q))
            self.assertTrue(np.allclose(cost.b(), b))
            self.assertTrue(np.allclose(cost.c(), c))

        check_quadratic_cost(qc, [1.], [2.], 3.)
        qc.UpdateCoefficients([10.], [20.])
        check_quadratic_cost(qc, [10.], [20.], 0)

        qc.UpdateCoefficients([-10.], [20.])
        self.assertFalse(qc.is_convex())

        qc.UpdateCoefficients([10.], [20.], is_convex=True)
        self.assertTrue(qc.is_convex())

    def test_eval_binding(self):
        qp = TestQP()
        prog = qp.prog

        x = qp.x
        x_expected = np.array([1., 1.])

        costs = qp.costs
        cost_values_expected = [2., 1.]
        constraints = qp.constraints
        constraint_values_expected = [1., 1., 2., 3.]

        result = mp.Solve(prog)
        self.assertTrue(np.allclose(result.GetSolution(x), x_expected))

        enum = zip(constraints, constraint_values_expected)
        for (constraint, value_expected) in enum:
            value = result.EvalBinding(constraint)
            self.assertTrue(np.allclose(value, value_expected))
            value = prog.EvalBinding(constraint, x_expected)
            self.assertTrue(np.allclose(value, value_expected))
            value = prog.EvalBindingVectorized(
                constraint,
                np.vstack((x_expected, x_expected)).T)
            a = np.vstack((value_expected, value_expected)).T
            self.assertTrue(np.allclose(
                value, np.vstack((value_expected, value_expected)).T))

        enum = zip(costs, cost_values_expected)
        for (cost, value_expected) in enum:
            value = result.EvalBinding(cost)
            self.assertTrue(np.allclose(value, value_expected))
            value = prog.EvalBinding(cost, x_expected)
            self.assertTrue(np.allclose(value, value_expected))
            value = prog.EvalBindingVectorized(
                cost,
                np.vstack((x_expected, x_expected)).T)
            self.assertTrue(np.allclose(
                value, np.vstack((value_expected, value_expected)).T))

        self.assertIsInstance(
            result.EvalBinding(costs[0]), np.ndarray)

        # Bindings for `Eval`.
        x_list = (float(1.), AutoDiffXd(1.), sym.Variable("x"))
        T_y_list = (float, AutoDiffXd, sym.Expression)
        evaluator = costs[0].evaluator()
        for x_i, T_y_i in zip(x_list, T_y_list):
            y_i = evaluator.Eval(x=[x_i, x_i])
            self.assertIsInstance(y_i[0], T_y_i)

    def test_get_binding_variable_values(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        binding1 = prog.AddBoundingBoxConstraint(-1, 1, x[0])
        binding2 = prog.AddLinearEqualityConstraint(x[1] + 2*x[2], 2)
        x_val = np.array([-2., 1., 2.])
        np.testing.assert_allclose(
            prog.GetBindingVariableValues(binding1, x_val), np.array([-2]))
        np.testing.assert_allclose(
            prog.GetBindingVariableValues(binding2, x_val), np.array([1, 2]))

    def test_prog_check_satisfied(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        binding1 = prog.AddBoundingBoxConstraint(-1, 1, x[0])
        binding2 = prog.AddLinearConstraint(x[1]+x[2] <= 2.0)
        x_val = np.array([-2, .1, .3])
        self.assertTrue(
            prog.CheckSatisfied(binding=binding2, prog_var_vals=x_val,
                                tol=0.0))
        self.assertFalse(
            prog.CheckSatisfied(bindings=[binding1, binding2],
                                prog_var_vals=x_val,
                                tol=0.0))
        prog.SetInitialGuessForAllVariables(x_val)
        self.assertTrue(
            prog.CheckSatisfiedAtInitialGuess(binding=binding2, tol=0.0))
        self.assertFalse(
            prog.CheckSatisfiedAtInitialGuess(
                bindings=[binding1, binding2], tol=0.0))

    def test_matrix_variables(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, 2, "x")
        for i in range(2):
            for j in range(2):
                prog.AddLinearConstraint(x[i, j] == 2 * i + j)
        result = mp.Solve(prog)
        xval = result.GetSolution(x)
        for i in range(2):
            for j in range(2):
                self.assertAlmostEqual(xval[i, j], 2 * i + j)
                self.assertEqual(xval[i, j], result.GetSolution(x[i, j]))
        # Just check spelling.
        y = prog.NewIndeterminates(2, 2, "y")

    def test_linear_constraint(self):
        A = np.array([[1, 3, 4], [2., 4., 5]])
        lb = np.array([1, 2.])
        ub = np.array([3., 4.])
        # Constructor with dense A.
        dut = mp.LinearConstraint(A=A, lb=lb, ub=ub)
        self.assertEqual(dut.num_constraints(), 2)
        self.assertEqual(dut.num_vars(), 3)
        np.testing.assert_array_equal(dut.get_sparse_A().todense(), A)

        A_sparse = scipy.sparse.csc_matrix(
            (np.array([2, 1., 3]), np.array([0, 1, 0]),
             np.array([0, 2, 2, 3])), shape=(2, 3))
        dut = mp.LinearConstraint(
            A=A_sparse, lb=np.array([1., 2.]), ub=np.array([2., 3.]))
        self.assertEqual(dut.num_constraints(), 2)
        self.assertEqual(dut.num_vars(), 3)
        self.assertEqual(dut.get_sparse_A().nnz, 3)

        dut.UpdateCoefficients(
            new_A=A_sparse, new_lb=np.array([2, 3.]),
            new_ub=np.array([3., 4.]))
        np.testing.assert_array_equal(
            dut.get_sparse_A().todense(), A_sparse.todense())

        dut.UpdateCoefficients(
            new_A=np.array([[1E-10, 0, 0], [0, 1, 1]]),
            new_lb=np.array([2, 3]), new_ub=np.array([3, 4]))
        dut.RemoveTinyCoefficient(tol=1E-5)
        np.testing.assert_array_equal(
            dut.GetDenseA(), np.array([[0, 0, 0], [0, 1, 1]]))

    def test_linear_equality_constraint(self):
        Aeq = np.array([[2, 3.], [1., 2.], [3, 4]])
        beq = np.array([1., 2., 3.])
        constraint = mp.LinearEqualityConstraint(Aeq=Aeq, beq=beq)
        np.testing.assert_array_equal(constraint.GetDenseA(), Aeq)
        np.testing.assert_array_equal(constraint.upper_bound(), beq)
        constraint.UpdateCoefficients(Aeq=Aeq, beq=beq)

        constraint = mp.LinearEqualityConstraint(
            a=np.array([1., 2., 3.]), beq=1)
        np.testing.assert_array_equal(
            constraint.GetDenseA(), np.array([[1., 2., 3.]]))
        np.testing.assert_array_equal(constraint.upper_bound(), np.array([1.]))

        A_sparse = scipy.sparse.csc_matrix(
            (np.array([2, 1., 3]), np.array([0, 1, 0]),
             np.array([0, 2, 2, 3])), shape=(2, 3))
        dut = mp.LinearEqualityConstraint(Aeq=A_sparse, beq=np.array([1, 2.]))
        np.testing.assert_array_equal(
            dut.get_sparse_A().todense(), A_sparse.todense())

        dut.UpdateCoefficients(A_sparse, beq=np.array([1, 2.]))
        self.assertFalse(dut.is_dense_A_constructed())

    def test_sdp(self):
        prog = mp.MathematicalProgram()
        S = prog.NewSymmetricContinuousVariables(3, "S")
        prog.AddLinearConstraint(S[0, 1] >= 1)
        prog.AddPositiveSemidefiniteConstraint(S)
        minor_indices = {0, 2}
        self.assertEqual(len(prog.positive_semidefinite_constraints()), 1)
        self.assertEqual(
            prog.positive_semidefinite_constraints()[0].evaluator().
            matrix_rows(), 3)
        prog.AddPrincipalSubmatrixIsPsdConstraint(S, minor_indices)
        self.assertEqual(len(prog.positive_semidefinite_constraints()), 2)
        self.assertEqual(
            prog.positive_semidefinite_constraints()[1].evaluator().
            matrix_rows(), 2)
        prog.AddPositiveSemidefiniteConstraint(S+S)
        prog.AddPrincipalSubmatrixIsPsdConstraint(S+S, minor_indices)
        prog.AddPositiveDiagonallyDominantMatrixConstraint(X=S)
        prog.AddPositiveDiagonallyDominantDualConeMatrixConstraint(X=S)
        prog.AddPositiveDiagonallyDominantDualConeMatrixConstraint(X=S+S)
        prog.AddScaledDiagonallyDominantMatrixConstraint(X=S)
        prog.AddScaledDiagonallyDominantMatrixConstraint(X=S+S)
        prog.AddScaledDiagonallyDominantDualConeMatrixConstraint(X=S)
        prog.AddScaledDiagonallyDominantDualConeMatrixConstraint(X=S+S)
        prog.AddLinearCost(np.trace(S))
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())
        S = result.GetSolution(S)
        eigs = np.linalg.eigvals(S)
        tol = 1e-8
        self.assertTrue(np.all(eigs >= -tol))
        self.assertTrue(S[0, 1] >= -tol)

    def test_replace_psd_methods(self):
        prog = mp.MathematicalProgram()
        replacement_methods = [
            prog.TightenPsdConstraintToDd,
            prog.TightenPsdConstraintToSdd,
            prog.RelaxPsdConstraintToDdDualCone,
            prog.RelaxPsdConstraintToSddDualCone,
        ]
        for method in replacement_methods:
            X = prog.NewSymmetricContinuousVariables(3)
            psd_constraint = prog.AddPositiveSemidefiniteConstraint(X)
            self.assertEqual(len(prog.positive_semidefinite_constraints()), 1)
            method(constraint=psd_constraint)
            self.assertEqual(len(prog.positive_semidefinite_constraints()), 0)

    def test_sos_polynomial(self):
        # Only check if the API works.
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(3, "x")
        (poly1, gramian1) = prog.NewSosPolynomial(
            indeterminates=sym.Variables(x), degree=4,
            type=mp.MathematicalProgram.NonnegativePolynomial.kSdsos,
            gram_name="M0")
        self.assertIsInstance(poly1, sym.Polynomial)
        self.assertIsInstance(gramian1, np.ndarray)

        gramian2 = prog.NewSymmetricContinuousVariables(2)
        poly2 = prog.NewSosPolynomial(
            gramian=gramian2,
            monomial_basis=(sym.Monomial(x[0]), sym.Monomial(x[1])),
            type=mp.MathematicalProgram.NonnegativePolynomial.kDsos)
        self.assertIsInstance(gramian2, np.ndarray)

        poly3, gramian3 = prog.NewSosPolynomial(
            monomial_basis=(sym.Monomial(x[0]), sym.Monomial(x[1])),
            type=mp.MathematicalProgram.NonnegativePolynomial.kSos,
            gram_name="M2")
        self.assertIsInstance(poly3, sym.Polynomial)
        self.assertIsInstance(gramian3, np.ndarray)

    def test_new_even_degree_nonnegative_polynomial(self):
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(3, "x")
        for poly_type in (mp.MathematicalProgram.NonnegativePolynomial.kSos,
                          mp.MathematicalProgram.NonnegativePolynomial.kSdsos,
                          mp.MathematicalProgram.NonnegativePolynomial.kDsos):
            poly, gram_odd, gram_even = (
                prog.NewEvenDegreeNonnegativePolynomial(
                    indeterminates=sym.Variables(x), degree=2, type=poly_type
                )
            )
            self.assertIsInstance(poly, sym.Polynomial)
            self.assertIsInstance(gram_odd, np.ndarray)
            self.assertIsInstance(gram_even, np.ndarray)

    def test_add_sos_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(1, "x")
        Q = prog.AddSosConstraint(
           p=sym.Polynomial(x[0]**2 + 1),
           monomial_basis=[sym.Monomial(x[0])],
           type=mp.MathematicalProgram.NonnegativePolynomial.kSdsos,
           gram_name="Q")
        Q, m = prog.AddSosConstraint(
            p=sym.Polynomial(x[0]**2 + 2),
            type=mp.MathematicalProgram.NonnegativePolynomial.kSdsos,
            gram_name="Q")

    def test_sos(self):
        # Find a,b,c,d subject to
        # a(0) + a(1)*x,
        # b(0) + 2*b(1)*x + b(2)*x^2 is SOS,
        # c(0)*x^2 + 2*c(1)*x*y + c(2)*y^2 is SOS,
        # d(0)*x^2 is SOS.
        # d(1)*x^2 is SOS.
        # d(0) + d(1) = 1
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(1, "x")
        self.assertEqual(prog.indeterminates_index()[x[0].get_id()], 0)
        poly = prog.NewFreePolynomial(sym.Variables(x), 1)
        (poly, binding) = prog.NewSosPolynomial(
            indeterminates=sym.Variables(x), degree=2, gram_name="M0")
        even_poly = prog.NewEvenDegreeFreePolynomial(sym.Variables(x), 2)
        odd_poly = prog.NewOddDegreeFreePolynomial(sym.Variables(x), 3)
        y = prog.NewIndeterminates(1, "y")
        self.assertEqual(prog.indeterminates_index()[y[0].get_id()], 1)
        (poly, binding) = prog.NewSosPolynomial(
            monomial_basis=(sym.Monomial(x[0]), sym.Monomial(y[0])),
            gram_name="M1")
        d = prog.NewContinuousVariables(2, "d")
        prog.AddSosConstraint(d[0]*x.dot(x), gram_name="Q1")
        prog.AddSosConstraint(
            d[1]*x.dot(x), [sym.Monomial(x[0])], gram_name="Q2")
        prog.AddLinearEqualityConstraint(d[0] + d[1] == 1)
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())
        result.GetSolution(poly)

        (poly, Q_oo, Q_ee) = prog.NewEvenDegreeSosPolynomial(
            indeterminates=sym.Variables(x), degree=2)
        (poly, Q_oo, Q_ee) = prog.NewEvenDegreeSdsosPolynomial(
            indeterminates=sym.Variables(x), degree=2)
        (poly, Q_oo, Q_ee) = prog.NewEvenDegreeDsosPolynomial(
            indeterminates=sym.Variables(x), degree=2)

    def test_make_polynomial(self):
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(1, "x")[0]
        a = prog.NewContinuousVariables(1, "a")[0]
        # e = (a + 1)x² + 2ax + 3a.
        e = (a + 1) * (x * x) + (2 * a) * x + 3 * a

        # We create a polynomial of `e` via MakePolynomial.
        p = prog.MakePolynomial(e)
        # Check its indeterminates and decision variables are correctly set,
        self.assertEqual(p.indeterminates().size(), 1)
        self.assertTrue(p.indeterminates().include(x))
        self.assertEqual(p.decision_variables().size(), 1)
        self.assertTrue(p.decision_variables().include(a))
        # Check if it holds the same expression when converted back to
        # symbolic expression.
        self.assertTrue(p.ToExpression().EqualTo(e))

    def test_reparse(self):
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(1, "x")[0]
        a = prog.NewContinuousVariables(1, "a")[0]
        e = (a + 1) * (x * x) + (2 * a) * x + 3 * a

        # p = (x^2 + 2x + 3)a + x^2 with indeterminates {a}.
        p = sym.Polynomial(e, [a])
        self.assertEqual(p.TotalDegree(), 1)

        # p = (a + 1)x² + 2ax + 3a with indeterminates {x}.
        prog.Reparse(p)
        self.assertEqual(p.TotalDegree(), 2)

    def test_equality_between_polynomials(self):
        prog = mp.MathematicalProgram()
        x = prog.NewIndeterminates(1, "x")
        a = prog.NewContinuousVariables(2, "a")
        linear_eq_constraints = prog.AddEqualityConstraintBetweenPolynomials(
            sym.Polynomial(2 * a[0] * x[0] + a[1] + 2, x),
            sym.Polynomial(2 * x[0] + 4, x))
        self.assertEqual(len(linear_eq_constraints), 2)
        result = mp.Solve(prog)
        a_val = result.GetSolution(a)
        self.assertAlmostEqual(a_val[0], 1)
        self.assertAlmostEqual(a_val[1], 2)

    def test_log_determinant_cost(self):
        # Find the minimal ellipsoid that covers some given points.
        prog = mp.MathematicalProgram()
        X = prog.NewSymmetricContinuousVariables(2)
        pts = np.array([[1, 1], [1, -1], [-1, 1]])
        for i in range(3):
            pt = pts[i, :]
            prog.AddLinearConstraint(pt.dot(X.dot(pt)) <= 1)
        linear_cost, log_det_t, log_det_Z = \
            prog.AddMaximizeLogDeterminantCost(X=X)
        self.assertEqual(log_det_t.shape, (2,))
        self.assertEqual(log_det_Z.shape, (2, 2))
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())

    def test_log_determinant_lower(self):
        prog = mp.MathematicalProgram()
        X = prog.NewSymmetricContinuousVariables(2)
        linear_constraint, t, Z = prog.AddLogDeterminantLowerBoundConstraint(
            X=X, lower=1)
        self.assertEqual(t.shape, (2,))
        self.assertEqual(Z.shape, (2, 2))

    def test_maximize_geometric_mean(self):
        # Find the smallest axis-algined ellipsoid that covers some given
        # points.
        prog = mp.MathematicalProgram()
        a = prog.NewContinuousVariables(2)
        pts = np.array([[1, 1], [1, -1], [-1, 1]])
        for i in range(3):
            pt = pts[i, :]
            prog.AddLinearConstraint(pt.dot(a * pt) <= 1)
        cost = prog.AddMaximizeGeometricMeanCost(a, 1)
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())
        self.assertIsInstance(cost, mp.Binding[mp.LinearCost])

    def test_max_geometric_mean_trivial(self):
        # Solve the trivial problem.
        # max (2x+3)*(3x+2)
        # s.t 2x+3 >= 0
        #     3x+2 >= 0
        #     x <= 10
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1)
        prog.AddLinearConstraint(x[0] <= 10)
        A = np.array([2, 3])
        b = np.array([3, 2])
        prog.AddMaximizeGeometricMeanCost(A, b, x)
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())

    def test_lcp(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, 'x')
        M = np.array([[1, 3], [4, 1]])
        q = np.array([-16, -15])
        binding = prog.AddLinearComplementarityConstraint(M, q, x)
        np.testing.assert_equal(binding.evaluator().M(), M)
        np.testing.assert_equal(binding.evaluator().q(), q)
        self.assertEqual(len(prog.linear_complementarity_constraints()), 1)
        result = mp.Solve(prog)
        self.assertTrue(result.is_success())
        self.assertIsInstance(binding.evaluator(),
                              mp.LinearComplementarityConstraint)

    def test_add_exponential_cone_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2)
        A = np.array([[1., 2.], [2., 3.], [0., 1.]])
        b = np.array([1., 2., 3.])
        constraint1 = prog.AddExponentialConeConstraint(A=A, b=b, vars=x)
        np.testing.assert_array_equal(constraint1.evaluator().A(), A)
        np.testing.assert_array_equal(constraint1.evaluator().b(), b)

        constraint2 = prog.AddExponentialConeConstraint(
            z=np.array([x[0] + 1, x[0] * 2, x[1] + 2]))
        self.assertIsInstance(
            constraint2.evaluator(), mp.ExponentialConeConstraint)

        self.assertEqual(len(prog.exponential_cone_constraints()), 2)

        constraint3 = mp.ExponentialConeConstraint(A=A, b=b)
        np.testing.assert_array_equal(constraint3.A(), A)
        np.testing.assert_array_equal(constraint3.b(), b)

    def test_linear_constraints(self):
        # TODO(eric.cousineau): Add more general tests
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, 'x')
        lb = [0., 0.]
        ub = [1., 1.]

        prog.AddBoundingBoxConstraint(lb, ub, x)
        prog.AddBoundingBoxConstraint(0., 1., x[0])
        prog.AddBoundingBoxConstraint(0., 1., x)

        A_dense = np.eye(2)
        dense1 = prog.AddLinearConstraint(
            A=A_dense, lb=np.zeros(2), ub=np.ones(2), vars=x)
        # Ensure that the dense version of the binding has been called.
        self.assertTrue(dense1.evaluator().is_dense_A_constructed())

        A_sparse = scipy.sparse.csc_matrix(A_dense)
        sparse1 = prog.AddLinearConstraint(
            A=A_sparse, lb=np.zeros(2), ub=np.ones(2), vars=x)
        # Ensure that the sparse version of the binding has been called.
        self.assertFalse(sparse1.evaluator().is_dense_A_constructed())
        prog.AddLinearConstraint(a=[1, 1], lb=0, ub=0, vars=x)
        prog.AddLinearConstraint(e=x[0], lb=0, ub=1)
        prog.AddLinearConstraint(v=x, lb=[0, 0], ub=[1, 1])
        prog.AddLinearConstraint(f=(x[0] == 0))

        dense2 = prog.AddLinearEqualityConstraint(
            Aeq=A_dense, beq=np.zeros(2), vars=x)
        # Ensure that the dense version of the binding has been called.
        self.assertTrue(dense2.evaluator().is_dense_A_constructed())

        sparse2 = prog.AddLinearEqualityConstraint(
            Aeq=A_sparse, beq=np.zeros(2), vars=x)
        # Ensure that the sparse version of the binding has been called.
        self.assertFalse(sparse2.evaluator().is_dense_A_constructed())
        prog.AddLinearEqualityConstraint(a=[1, 1], beq=0, vars=x)
        prog.AddLinearEqualityConstraint(f=x[0] == 1)
        prog.AddLinearEqualityConstraint(formulas=[x[0] == 1, x[0] == 1])
        prog.AddLinearEqualityConstraint(e=x[0] + x[1], b=1)
        prog.AddLinearEqualityConstraint(
            v=2 * x[:2] + np.array([0, 1]), b=np.array([3, 2]))

    def test_constraint_set_bounds(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")

        def constraint(x):
            return x[1] ** 2
        binding = prog.AddConstraint(constraint, [0], [1], vars=x)
        self.assertIsInstance(binding.evaluator(), PyFunctionConstraint)
        np.testing.assert_array_equal(
            binding.evaluator().lower_bound(), np.array([0.]))
        np.testing.assert_array_equal(
            binding.evaluator().upper_bound(), np.array([1.]))
        # Test UpdateLowerBound()
        binding.evaluator().UpdateLowerBound(new_lb=[-1.])
        np.testing.assert_array_equal(
            binding.evaluator().lower_bound(), np.array([-1.]))
        np.testing.assert_array_equal(
            binding.evaluator().upper_bound(), np.array([1.]))
        # Test UpdateLowerBound()
        binding.evaluator().UpdateUpperBound(new_ub=[2.])
        np.testing.assert_array_equal(
            binding.evaluator().lower_bound(), np.array([-1.]))
        np.testing.assert_array_equal(
            binding.evaluator().upper_bound(), np.array([2.]))
        # Test set_bounds()
        binding.evaluator().set_bounds(lower_bound=[-3.], upper_bound=[4.])
        np.testing.assert_array_equal(
            binding.evaluator().lower_bound(), np.array([-3.]))
        np.testing.assert_array_equal(
            binding.evaluator().upper_bound(), np.array([4.]))

    def test_constraint_gradient_sparsity(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")

        def cost(x):
            return x[0]**2

        def constraint(x):
            return x[1] ** 2

        cost_binding = prog.AddCost(cost, vars=x)
        constraint_binding = prog.AddConstraint(constraint, [0], [1], vars=x)
        cost_evaluator = cost_binding.evaluator()
        constraint_evaluator = constraint_binding.evaluator()
        self.assertIsNone(cost_evaluator.gradient_sparsity_pattern())
        self.assertIsNone(constraint_evaluator.gradient_sparsity_pattern())
        # Now set the sparsity
        cost_evaluator.SetGradientSparsityPattern([(0, 0)])
        self.assertEqual(cost_evaluator.gradient_sparsity_pattern(), [(0, 0)])
        constraint_binding.evaluator().SetGradientSparsityPattern([(0, 1)])
        self.assertEqual(
            constraint_evaluator.gradient_sparsity_pattern(),
            [(0, 1)])

    def test_pycost_and_pyconstraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')

        def cost(x):
            return (x[0]-1.)*(x[0]-1.)

        def constraint(x):
            return x

        cost_binding = prog.AddCost(cost, vars=x)
        constraint_binding = prog.AddConstraint(
            constraint, lb=[0.], ub=[2.], vars=x)
        result = mp.Solve(prog)
        xstar = result.GetSolution(x)
        self.assertAlmostEqual(xstar[0], 1.)

        # Verify that they can be evaluated.
        self.assertAlmostEqual(cost_binding.evaluator().Eval(xstar), 0.)
        self.assertAlmostEqual(constraint_binding.evaluator().Eval(xstar), 1.)
        self.assertEqual(len(prog.generic_constraints()), 1)
        self.assertEqual(
            prog.generic_constraints()[0].evaluator(),
            constraint_binding.evaluator())
        self.assertEqual(len(prog.generic_costs()), 1)
        self.assertEqual(
            prog.generic_costs()[0].evaluator(), cost_binding.evaluator())

    def test_cost_and_constraint_python_wrapper_lost(self):
        # Ensure cost and constraints python wrappers are kept alive when added
        # to a mathematical program. See issue #20131 for original problem
        # description.

        # Make some objects in a function to let most of them be deleted at
        # scope exit. We will test if the contents of the first return value
        # ("keepers") succeed in keeping alive the objects tracked by "spies".
        def make_object_graph():
            spies = []
            prog = mp.MathematicalProgram()
            x = prog.NewContinuousVariables(1, 'x')

            cost = mp.LinearCost([1.0], 0.0)
            spies.append(weakref.finalize(cost, lambda: None))

            constraint = mp.LinearConstraint(np.array([1.0]), np.array([1]),
                                             np.array([np.inf]))
            spies.append(weakref.finalize(constraint, lambda: None))

            cost_binding = prog.AddCost(cost, vars=x)
            constraint_binding = prog.AddConstraint(constraint, vars=x)
            return [cost_binding, constraint_binding], spies

        keepers, spies = make_object_graph()
        self.assertTrue(all(spy.alive for spy in spies))

    def get_different_scalar_type(self, T):
        # Gets U such that U != T.
        next_index = SCALAR_TYPES.index(T) + 1
        U = SCALAR_TYPES[next_index % len(SCALAR_TYPES)]
        self.assertNotEqual(U, T)
        return U

    def test_pycost_wrap_error(self):
        """Tests for checks using PyFunctionCost::Wrap."""
        # TODO(eric.cousineau): It would be nice to not need a
        # MathematicalProgram to test these.

        def user_cost_bad_shape(x):
            # WARNING: This should return a scalar, not a vector!
            return x

        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')
        binding_bad_shape = prog.AddCost(user_cost_bad_shape, vars=x)

        for T in SCALAR_TYPES:
            array_T = np.vectorize(T)
            x0 = array_T([0.])
            x0_bad = array_T([0., 1.])
            # Bad input (before function is called).
            if kDrakeAssertIsArmed:
                # See note in `WrapUserFunc`.
                input_error_cls = SystemExit
                input_error_expected = (
                    "x.rows() == num_vars_ || num_vars_ == Eigen::Dynamic")
            else:
                input_error_cls = RuntimeError
                input_error_expected = (
                    "PyFunctionCost: Input must be of .ndim = 1 or 2 (vector) "
                    "and .size = 1. Got .ndim = 1 and .size = 2 instead.")
            with self.assertRaises(input_error_cls) as cm:
                binding_bad_shape.evaluator().Eval(x0_bad)
            self.assertIn(input_error_expected, str(cm.exception))
            # Bad output shape.
            with self.assertRaises(RuntimeError) as cm:
                binding_bad_shape.evaluator().Eval(x0)
            self.assertEqual(
                str(cm.exception),
                "PyFunctionCost: Return value must be of .ndim = 0 (scalar) "
                "and .size = 1. Got .ndim = 1 and .size = 1 instead.")

            # Bad output dtype.
            U = self.get_different_scalar_type(T)

            def user_cost_bad_dtype(x):
                # WARNING: This should return the same dtype as x!
                return U(0.)

            binding_bad_dtype = prog.AddCost(user_cost_bad_dtype, vars=x)
            with self.assertRaises(TypeError) as cm:
                binding_bad_dtype.evaluator().Eval(x0)
            self.assertEqual(
                str(cm.exception),
                f"When PyFunctionCost is called with an array of type "
                f"{T.__name__} the return value must be the same type, not "
                f"{U.__name__}.")

    def test_pyconstraint_wrap_error(self):
        """Tests for checks using PyFunctionConstraint::Wrap."""
        # TODO(eric.cousineau): It would be nice to not need a
        # MathematicalProgram to test these.

        def user_constraint_bad_shape(x):
            # WARNING: This should return a vector, not a scalar!
            return x[0]

        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')
        binding_bad_shape = prog.AddConstraint(
            user_constraint_bad_shape, lb=[0.], ub=[2.], vars=x)

        for T in SCALAR_TYPES:
            array_T = np.vectorize(T)
            x0 = array_T([0.])
            x0_bad = array_T([0., 1.])
            # Bad input (before function is called).
            if kDrakeAssertIsArmed:
                # See note in `WrapUserFunc`.
                input_error_cls = SystemExit
                input_error_expected = (
                    "x.rows() == num_vars_ || num_vars_ == Eigen::Dynamic")
            else:
                input_error_cls = RuntimeError
                input_error_expected = (
                    "PyFunctionConstraint: Input must be of .ndim = 1 or 2 "
                    "(vector) and .size = 1. Got .ndim = 1 and .size = 2 "
                    "instead.")
            with self.assertRaises(input_error_cls) as cm:
                binding_bad_shape.evaluator().Eval(x0_bad)
            self.assertIn(input_error_expected, str(cm.exception))
            # Bad output.
            with self.assertRaises(RuntimeError) as cm:
                binding_bad_shape.evaluator().Eval(x0)
            self.assertEqual(
                str(cm.exception),
                "PyFunctionConstraint: Return value must be of .ndim = 1 or 2 "
                "(vector) and .size = 1. Got .ndim = 0 and .size = 1 instead.")

            # Bad output dtype.
            U = self.get_different_scalar_type(T)

            def user_constraint_bad_dtype(x):
                # WARNING: This should return the same dtype as x!
                return [U(0.)]

            binding_bad_dtype = prog.AddConstraint(
                user_constraint_bad_dtype, lb=[0.], ub=[2.], vars=x)
            with self.assertRaises(TypeError) as cm:
                binding_bad_dtype.evaluator().Eval(x0)
            self.assertEqual(
                str(cm.exception),
                f"When PyFunctionConstraint is called with an array of type "
                f"{T.__name__} the return value must be the same type, not "
                f"{U.__name__}.")

    def test_addcost_symbolic(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')
        prog.AddCost((x[0]-1.)**2)
        prog.AddConstraint(0 <= x[0])
        prog.AddConstraint(x[0] <= 2)
        result = mp.Solve(prog)
        self.assertAlmostEqual(result.GetSolution(x)[0], 1.)

    def test_add_l2norm_cost(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, 'x')
        A = np.array([[1, 2.], [3., 4]])
        b = np.array([1., 2.])
        prog.AddL2NormCost(A=A, b=b, vars=x)
        self.assertEqual(len(prog.l2norm_costs()), 1)
        prog.AddL2NormCost(
            e=np.linalg.norm(A@x+b), psd_tol=1e-8, coefficient_tol=1e-8)
        self.assertEqual(len(prog.l2norm_costs()), 2)
        prog.AddCost(e=np.linalg.norm(A@x+b))
        self.assertEqual(len(prog.l2norm_costs()), 3)

    def test_add_l2norm_cost_using_conic_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")
        s, linear_cost, lorentz_cone_constraint = \
            prog.AddL2NormCostUsingConicConstraint(
                A=np.array([[1, 2.], [3., 4]]),
                b=np.array([1., 2.]), vars=x)
        self.assertEqual(len(prog.linear_costs()), 1)
        self.assertEqual(len(prog.lorentz_cone_constraints()), 1)
        self.assertEqual(prog.num_vars(), 3)

    def test_addcost_shared_ptr(self):
        # In particular, confirm that LinearCost ends up in linear_costs, etc.
        # as opposed to everything ending up as a generic_cost.
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, "x")
        binding = prog.AddCost(obj=mp.LinearCost([1.0], 0.0), vars=x)
        # Would be great if this was Binding[LinearCost], but we currently
        # expect it to be only Binding[Cost]
        self.assertIsInstance(binding, mp.Binding[mp.Cost])
        self.assertIsInstance(binding.evaluator(), mp.LinearCost)
        self.assertEqual(len(prog.linear_costs()), 1)

        binding = prog.AddCost(mp.QuadraticCost([[1.0]], [0.0], 0.0), x)
        # Would be great if this was Binding[QuadraticCost], but we currently
        # expect it to be only Binding[Cost]
        self.assertIsInstance(binding, mp.Binding[mp.Cost])
        self.assertIsInstance(binding.evaluator(), mp.QuadraticCost)
        self.assertEqual(len(prog.quadratic_costs()), 1)

        # Confirm that I can add an L2NormCost.
        binding = prog.AddCost(mp.L2NormCost([[1.0]], [0.0]), x)
        self.assertIsInstance(binding, mp.Binding[mp.Cost])
        self.assertIsInstance(binding.evaluator(), mp.L2NormCost)
        self.assertEqual(len(prog.generic_costs()), 0)
        self.assertEqual(len(prog.l2norm_costs()), 1)

    def test_addconstraint_matrix(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')
        prog.AddConstraint(np.array([[x[0] <= 2], [x[0] >= -2]]))
        result = mp.Solve(prog)
        self.assertTrue(result.GetSolution(x)[0] <= 2)
        self.assertTrue(result.GetSolution(x)[0] >= -2)

    def test_addconstraint_binding(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1, 'x')
        prog.AddConstraint(x[0] <= 2)
        # This ensures that constraint is of type Binding<Constraint> and not
        # a more specific type.
        constraint = prog.GetAllConstraints()[0]
        constraint2 = prog.AddConstraint(constraint)
        self.assertEqual(constraint, constraint2)

    def test_initial_guess(self):
        prog = mp.MathematicalProgram()
        count = 6
        shape = (2, 3)
        x = prog.NewContinuousVariables(count, 'x')
        x_matrix = x.reshape(shape)
        x0 = np.arange(count)
        x0_matrix = x0.reshape(shape)
        all_nan = np.full(x.shape, np.nan)
        self.assertTrue(np.isnan(prog.GetInitialGuess(x)).all())

        def check_and_reset():
            self.assertTrue((prog.GetInitialGuess(x) == x0).all())
            self.assertTrue(
                (prog.GetInitialGuess(x_matrix) == x0_matrix).all())
            prog.SetInitialGuess(x, all_nan)
            self.assertTrue(np.isnan(prog.GetInitialGuess(x)).all())

        # Test setting individual variables
        for i in range(count):
            prog.SetInitialGuess(x[i], x0[i])
            self.assertEqual(prog.GetInitialGuess(x[i]), x0[i])
        check_and_reset()

        # Test setting matrix values using both
        # 1d and 2d np arrays.
        prog.SetInitialGuess(x, x0)
        check_and_reset()
        prog.SetInitialGuess(x_matrix, x0_matrix)
        check_and_reset()

        # Test setting all values at once.
        prog.SetInitialGuessForAllVariables(x0)
        np.testing.assert_array_equal(prog.initial_guess(), x0)
        check_and_reset()

        # Check an extrinsic guess.  We sanity check changes to the guess using
        # loose "any" and "all" predicates rather than specific indices because
        # we should not presume how variables map into indices.
        guess = np.ndarray(count)
        guess.fill(np.nan)
        self.assertTrue(all([np.isnan(i) for i in guess]))
        prog.SetDecisionVariableValueInVector(x[0], x0[0], guess)
        self.assertFalse(all([np.isnan(i) for i in guess]))
        self.assertTrue(any([np.isnan(i) for i in guess]))
        prog.SetDecisionVariableValueInVector(x_matrix, x0_matrix, guess)
        self.assertFalse(any([np.isnan(i) for i in guess]))

    def test_quadratic_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        hessian_type = mp.QuadraticConstraint.HessianType.kPositiveSemidefinite
        constraint1 = prog.AddQuadraticConstraint(
            Q=np.eye(2), b=np.array([1., 2.]), lb=0., ub=1., vars=x[:2],
            hessian_type=hessian_type)
        self.assertEqual(len(prog.quadratic_constraints()), 1)

        hessian_type = mp.QuadraticConstraint.HessianType.kIndefinite
        constraint2 = prog.AddQuadraticConstraint(
            x[0] * x[0] - x[2] * x[2], 1, 2, hessian_type=hessian_type)
        self.assertEqual(len(prog.quadratic_constraints()), 2)

    @unittest.skipIf(
        SNOPT_NO_GUROBI,
        "SNOPT is unable to solve this problem (#10653).")
    def test_lorentz_cone_constraint(self):
        # Set Up Mathematical Program
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, "x")
        z = prog.NewContinuousVariables(1, "z")
        prog.AddCost(z[0])

        # Add LorentzConeConstraints
        prog.AddLorentzConeConstraint(
            f=(z[0] >= np.linalg.norm(x)),
            eval_type=mp.LorentzConeConstraint.EvalType.kConvexSmooth,
            psd_tol=1e-7,
            coefficient_tol=1e-7)
        prog.AddLorentzConeConstraint(np.array([0*x[0]+1, x[0]-1, x[1]-1]))
        prog.AddLorentzConeConstraint(np.array([z[0], x[0], x[1]]))
        self.assertEqual(len(prog.lorentz_cone_constraints()), 3)

        # Test result
        # The default initial guess is [0, 0, 0]. This initial guess is bad
        # because LorentzConeConstraint with eval_type=kConvex is not
        # differentiable at [0, 0, 0]. Use initial guess [0.5, 0.5, 0.5]
        # instead.
        result = mp.Solve(prog, [0.5, 0.5, 0.5])
        self.assertTrue(result.is_success())

        # Check answer
        x_expected = np.array([1-2**(-0.5), 1-2**(-0.5)])
        self.assertTrue(np.allclose(result.GetSolution(x), x_expected))

    def test_add_lorentz_cone_constraint(self):
        # Call AddLorentzConeConstraint, make sure no error is thrown.
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)

        prog.AddLorentzConeConstraint(
            v=np.array([x[0]+1, x[1]+x[2], 2*x[1]]),
            eval_type=mp.LorentzConeConstraint.EvalType.kConvexSmooth)
        prog.AddLorentzConeConstraint(
            linear_expression=x[0] + x[1] + 1,
            quadratic_expression=x[0]*x[0] + x[1] * x[1] + 2 * x[0] * x[1] + 1,
            tol=0., eval_type=mp.LorentzConeConstraint.EvalType.kConvexSmooth)
        A = np.array([[1, 0], [0, 1], [1, 0], [0, 0]])
        b = np.array([1, 1, 0, 2])
        constraint = prog.AddLorentzConeConstraint(
            A=A, b=b, vars=x[:2],
            eval_type=mp.LorentzConeConstraint.EvalType.kConvexSmooth)
        np.testing.assert_allclose(
            constraint.evaluator().A().todense(), A)
        np.testing.assert_allclose(constraint.evaluator().b(), b)

    def test_add_rotated_lorentz_cone_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)

        A = np.array([[1, 0], [1, 1], [1, 0], [0, 1], [0, 0]])
        b = np.array([1, 0, 1, 0, 2])
        constraint = prog.AddRotatedLorentzConeConstraint(A=A, b=b, vars=x[:2])
        self.assertEqual(len(prog.rotated_lorentz_cone_constraints()), 1)
        np.testing.assert_allclose(
            constraint.evaluator().A().todense(), A)
        np.testing.assert_allclose(constraint.evaluator().b(), b)

        prog.AddRotatedLorentzConeConstraint(
            v=[x[0]+1, x[0]+x[1], x[0], x[2]+1, 2])
        constraint = prog.AddRotatedLorentzConeConstraint(
            linear_expression1=x[0]+1, linear_expression2=x[0]+x[1],
            quadratic_expression=x[0]*x[0] + 2*x[0] + x[1]*x[1] + 5)

    def test_add_quadratic_as_rotated_lorentz_cone_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2)
        dut = prog.AddQuadraticAsRotatedLorentzConeConstraint(
            Q=np.array([[1, 2.], [2., 10.]]),
            b=np.array([1., 3.]),
            c=0.5,
            vars=x,
            psd_tol=0.)
        self.assertIsInstance(dut.evaluator(), mp.RotatedLorentzConeConstraint)

    def test_add_linear_matrix_inequality_constraint(self):
        prog = mp.MathematicalProgram()
        F = [np.eye(2), np.array([[0, 1], [1., 0.]])]
        x = prog.NewContinuousVariables(1)
        constraint = prog.AddLinearMatrixInequalityConstraint(F=F, vars=x)
        self.assertIsInstance(
            constraint.evaluator(), mp.LinearMatrixInequalityConstraint)
        self.assertEqual(constraint.evaluator().matrix_rows(), 2)
        self.assertEqual(len(constraint.evaluator().F()), 2)
        np.testing.assert_array_equal(constraint.evaluator().F()[0], F[0])
        np.testing.assert_array_equal(constraint.evaluator().F()[1], F[1])
        self.assertEqual(len(prog.linear_matrix_inequality_constraints()), 1)

        constraint2 = prog.AddLinearMatrixInequalityConstraint(
            X=np.array([
                [1, 2 + x[0], 3 - x[0]],
                [2 + x[0], 1 - x[0], 1],
                [3 - x[0], 1, 2]]))
        self.assertIsInstance(
            constraint2.evaluator(), mp.LinearMatrixInequalityConstraint)
        self.assertEqual(len(prog.linear_matrix_inequality_constraints()), 2)

    def test_solver_id(self):
        self.assertEqual(ScsSolver().solver_id(), ScsSolver().solver_id())
        self.assertNotEqual(ScsSolver().solver_id(), OsqpSolver().solver_id())
        # Test the hash function, by checking the set size.
        self.assertEqual(
            len({ScsSolver().solver_id(), ScsSolver().solver_id()}), 1)
        self.assertEqual(
            len({ScsSolver().solver_id(), OsqpSolver().solver_id()}), 2)

    def test_mathematical_program_solver_options(self):
        # To cover all of the bindings, we'll check the program's set and get
        # methods variously using either SolverId or SolverType.
        gurobi_id = GurobiSolver().solver_id()
        for solver in [gurobi_id, SolverType.kGurobi]:
            prog = mp.MathematicalProgram()
            prog.SetSolverOption(solver, "foxtrot", 1.0)
            prog.SetSolverOption(solver, "india", 2)
            prog.SetSolverOption(solver, "sierra", "3")
            expected = {"foxtrot": 1.0, "india": 2, "sierra": "3"}
            with catch_drake_warnings(expected_count=1):
                self.assertDictEqual(prog.GetSolverOptions(solver), expected)
            old_options = prog.solver_options()
            self.assertEqual(old_options.options, {
                gurobi_id.name(): expected,
            })
            new_options = copy.deepcopy(old_options)
            new_options.SetOption(gurobi_id, "india", 4)
            self.assertNotEqual(new_options, old_options)
            prog.SetSolverOptions(new_options)
            expected["india"] = 4
            with catch_drake_warnings(expected_count=1):
                self.assertDictEqual(prog.GetSolverOptions(solver), expected)
            self.assertEqual(old_options.options, {
                gurobi_id.name(): expected,
            })

    def test_solver_options(self):
        CSO = mp.CommonSolverOption
        dut = SolverOptions()
        solver_id = SolverId("dummy")
        dut.SetOption(solver_id=solver_id, key="float_key", value=1.0)
        dut.SetOption(solver_id=solver_id, key="int_key", value=2)
        dut.SetOption(solver_id=solver_id, key="str_key", value="3")
        dut.SetOption(CSO.kPrintToConsole, True)
        dut.SetOption(CSO.kPrintFileName, "print.log")
        dut.SetOption(CSO.kStandaloneReproductionFileName, "repro.txt")
        dut.SetOption(CSO.kMaxThreads, 4)
        expected_dummy = {
            "float_key": 1.0, "int_key": 2, "str_key": "3",
        }
        expected_common = {
            CSO.kPrintToConsole: True,
            CSO.kPrintFileName: "print.log",
            CSO.kStandaloneReproductionFileName: "repro.txt",
            CSO.kMaxThreads: 4,
        }
        self.assertEqual(dut.options, {
            "dummy": expected_dummy,
            "Drake": dict(
                (key.name, value)
                for key, value in expected_common.items()
            )
        })
        with catch_drake_warnings(expected_count=1):
            self.assertDictEqual(dut.GetOptions(solver_id), expected_dummy)
        with catch_drake_warnings(expected_count=1):
            self.assertEqual(dut.common_solver_options(), expected_common)
        with catch_drake_warnings(expected_count=1):
            self.assertEqual(dut.get_print_to_console(), True)
        with catch_drake_warnings(expected_count=1):
            self.assertEqual(dut.get_print_file_name(), "print.log")
        with catch_drake_warnings(expected_count=1):
            self.assertEqual(dut.get_standalone_reproduction_file_name(),
                             "repro.txt")
        with catch_drake_warnings(expected_count=1):
            self.assertEqual(dut.get_max_threads(), 4)
        self.assertTrue(dut == dut)
        self.assertFalse(dut != dut)
        copy.deepcopy(dut)
        roundtrip = eval(repr(dut), dict(SolverOptions=SolverOptions))
        self.assertEqual(roundtrip, dut)

    def test_solver_options_yaml(self):
        CSO = mp.CommonSolverOption
        dut = SolverOptions()
        id1 = SolverId("id1")
        id2 = SolverId("id2")
        dut.SetOption(id1, "some_double", 1.1)
        dut.SetOption(id1, "some_int", 2)
        dut.SetOption(id2, "some_string", "foo")
        dut.SetOption(CSO.kPrintFileName, "foo.txt")
        dut.SetOption(CSO.kPrintToConsole, 1)
        dut.SetOption(CSO.kStandaloneReproductionFileName, "bar.py")
        dut.SetOption(CSO.kMaxThreads, 2)

        # If you change either of these two string constants, then you must
        # make the same change to the C++ solver_options.cc unit test.
        py_expected = textwrap.dedent("""\
        options:
          Drake:
            kMaxThreads: !!int '2'
            kPrintFileName: !!str 'foo.txt'
            kPrintToConsole: !!int '1'
            kStandaloneReproductionFileName: !!str 'bar.py'
          id1:
            some_double: 1.1
            some_int: !!int '2'
          id2:
            some_string: !!str 'foo'
        """)
        cxx_expected = textwrap.dedent("""\
        options:
          Drake:
            kMaxThreads: !!int 2
            kPrintFileName: !!str foo.txt
            kPrintToConsole: !!int 1
            kStandaloneReproductionFileName: !!str bar.py
          id1:
            some_double: 1.1
            some_int: !!int 2
          id2:
            some_string: !!str foo
        """)

        # Check that Python can save and then re-load the options.
        self.maxDiff = None
        actual_written = yaml_dump_typed(dut)
        self.assertMultiLineEqual(py_expected, actual_written)
        readback = yaml_load_typed(data=actual_written, schema=SolverOptions)
        self.assertEqual(readback, dut)

        # Cross-check that the output written by the C++ unit test can be
        # re-loaded in Python.
        cxx_readback = yaml_load_typed(data=cxx_expected, schema=SolverOptions)
        self.assertEqual(cxx_readback, dut)

    def test_infeasible_constraints(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1)
        result = mp.Solve(prog)

        infeasible = result.GetInfeasibleConstraints(prog)
        self.assertEqual(len(infeasible), 0)

        infeasible = result.GetInfeasibleConstraints(prog, tol=1e-4)
        self.assertEqual(len(infeasible), 0)

        infeasible_names = result.GetInfeasibleConstraintNames(
                prog=prog, tol=1e-4)
        self.assertEqual(len(infeasible_names), 0)

    def test_add_indeterminates_and_decision_variables(self):
        prog = mp.MathematicalProgram()
        x0 = sym.Variable("x0")
        x1 = sym.Variable("x1")
        y0 = sym.Variable("y0")
        y1 = sym.Variable("y1")
        z = sym.Variable("z")
        a0 = sym.Variable("a0")
        a1 = sym.Variable("a1")
        prog.AddIndeterminates(new_indeterminates=np.array([x0, x1]))
        prog.AddIndeterminates(new_indeterminates=sym.Variables([y0, y1]))
        prog.AddIndeterminate(new_indeterminate=z)
        self.assertEqual(prog.num_indeterminates(), 5)
        self.assertEqual(prog.FindIndeterminateIndex(x0), 0)
        prog.AddDecisionVariables(np.array([a0, a1]))
        numpy_compare.assert_equal(prog.decision_variable(0), a0)
        numpy_compare.assert_equal(prog.decision_variables()[0], a0)
        numpy_compare.assert_equal(prog.decision_variables()[1], a1)
        numpy_compare.assert_equal(prog.indeterminates()[0], x0)
        numpy_compare.assert_equal(prog.indeterminate(1), x1)

    def test_required_capabilities(self):

        prog = mp.MathematicalProgram()
        X = prog.NewSymmetricContinuousVariables(3, "X")
        prog.AddPositiveSemidefiniteConstraint(X)

        prog.AddLinearConstraint(X[0, 0] >= 0)
        prog.AddLinearEqualityConstraint(X[1, 0] == 1)
        prog.AddLinearCost(X[0, 0])
        expected_attributes = [
            mp.ProgramAttribute.kLinearCost,
            mp.ProgramAttribute.kLinearEqualityConstraint,
            mp.ProgramAttribute.kLinearConstraint,
            mp.ProgramAttribute.kPositiveSemidefiniteConstraint]
        for attribute in expected_attributes:
            self.assertIn(attribute, prog.required_capabilities())
        for attribute in prog.required_capabilities():
            self.assertIn(attribute, expected_attributes)

    def test_make_first_available_solver(self):
        gurobi_solver = GurobiSolver()
        scs_solver = ScsSolver()
        if scs_solver.available() and scs_solver.enabled():
            solver = mp.MakeFirstAvailableSolver(
                [gurobi_solver.solver_id(), scs_solver.solver_id()])

    def test_variable_scaling(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2, 'x')
        scaling = prog.GetVariableScaling()
        self.assertIsInstance(scaling, dict)
        self.assertEqual(len(scaling), 0)
        prog.SetVariableScaling(var=x[0], s=2.0)
        scaling = prog.GetVariableScaling()
        self.assertEqual(len(scaling), 1)
        x0_index = prog.decision_variable_index()[x[0].get_id()]
        self.assertEqual(scaling[x0_index], 2.0)
        prog.ClearVariableScaling()
        scaling = prog.GetVariableScaling()
        self.assertEqual(len(scaling), 0)

    def test_remove_decision_variable(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        x1_index = prog.FindDecisionVariableIndex(x[1])
        x1_index_removed = prog.RemoveDecisionVariable(x[1])
        self.assertEqual(x1_index, x1_index_removed)
        self.assertEqual(prog.num_vars(), 2)

    def test_remove_cost(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        linear_cost1 = prog.AddLinearCost(x[0] + 2 * x[1])
        prog.RemoveCost(cost=linear_cost1)
        self.assertEqual(len(prog.linear_costs()), 0)

        quadratic_cost1 = prog.AddQuadraticCost(x[0] * x[0] + 2 * x[1] * x[1])
        quadratic_cost2 = prog.AddQuadraticCost(x[2] * x[2])
        prog.RemoveCost(cost=quadratic_cost1)
        self.assertEqual(len(prog.quadratic_costs()), 1)

        generic_cost1 = prog.AddCost(x[0] * x[1] * x[2])
        generic_cost2 = prog.AddCost(x[0] * x[1] * x[2] * x[2])
        prog.RemoveCost(cost=generic_cost2)
        self.assertEqual(len(prog.generic_costs()), 1)

    def test_binding_upcasting(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(1)

        # Add general evaluators, costs, and constraints, and take bindings for
        # them.
        evaluator = mp_testing.Binding[mp_testing.StubEvaluatorBase].Make(x)
        cost = prog.AddCost(x[0] ** 2)
        constraint = prog.AddConstraint(x[0] >= 0.0)

        mp_testing.AcceptBindingEvaluatorBase(evaluator)
        mp_testing.AcceptBindingEvaluatorBase(cost)
        mp_testing.AcceptBindingEvaluatorBase(constraint)

        mp_testing.AcceptBindingCost(cost)
        with self.assertRaises(TypeError):
            mp_testing.AcceptBindingCost(evaluator)
        with self.assertRaises(TypeError):
            mp_testing.AcceptBindingCost(constraint)

        mp_testing.AcceptBindingConstraint(constraint)
        with self.assertRaises(TypeError):
            mp_testing.AcceptBindingConstraint(evaluator)
        with self.assertRaises(TypeError):
            mp_testing.AcceptBindingConstraint(cost)

    def test_remove_constraint(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        X = prog.NewSymmetricContinuousVariables(3)

        lin_con = prog.AddLinearConstraint(x[0] + x[1] <= 1)
        prog.RemoveConstraint(constraint=lin_con)
        self.assertEqual(len(prog.linear_constraints()), 0)

        lin_eq_con = prog.AddLinearEqualityConstraint(x[0] + x[1] == 1)
        prog.RemoveConstraint(constraint=lin_eq_con)
        self.assertEqual(len(prog.linear_equality_constraints()), 0)

        bb_con = prog.AddBoundingBoxConstraint(0, 1, x)
        prog.RemoveConstraint(constraint=bb_con)
        self.assertEqual(len(prog.bounding_box_constraints()), 0)

        lorentz_con = prog.AddLorentzConeConstraint(x)
        prog.RemoveConstraint(constraint=lorentz_con)
        self.assertEqual(len(prog.lorentz_cone_constraints()), 0)

        rotated_lorentz_con = prog.AddRotatedLorentzConeConstraint(x)
        prog.RemoveConstraint(constraint=rotated_lorentz_con)
        self.assertEqual(len(prog.rotated_lorentz_cone_constraints()), 0)

        psd_con = prog.AddPositiveSemidefiniteConstraint(X)
        prog.RemoveConstraint(constraint=psd_con)
        self.assertEqual(len(prog.positive_semidefinite_constraints()), 0)

        lmi_con = prog.AddLinearMatrixInequalityConstraint(
            [np.eye(3), np.eye(3), 2 * np.ones((3, 3))], x[:2])
        prog.RemoveConstraint(constraint=lmi_con)
        self.assertEqual(len(prog.linear_matrix_inequality_constraints()), 0)

        exponential_con = prog.AddExponentialConeConstraint(
            A=np.array([[1, 3], [2, 4], [0, 1]]), b=np.array([0, 1, 3]),
            vars=x[:2])
        prog.RemoveConstraint(constraint=exponential_con)
        self.assertEqual(len(prog.exponential_cone_constraints()), 0)

        lcp_con = prog.AddLinearComplementarityConstraint(
            np.eye(3), np.ones((3,)), x)
        prog.RemoveConstraint(constraint=lcp_con)
        self.assertEqual(len(prog.linear_complementarity_constraints()), 0)

    def test_remove_visualization_callback(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(3)
        callback = prog.AddVisualizationCallback(
            lambda x_val: print(x_val[0]), x)
        count = prog.RemoveVisualizationCallback(callback=callback)
        self.assertEqual(count, 1)
        self.assertEqual(len(prog.visualization_callbacks()), 0)

    def test_get_program_type(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2)
        prog.AddLinearConstraint(x[0] + x[1] == 2)
        prog.AddQuadraticCost(x[0] ** 2, is_convex=True)
        self.assertEqual(mp.GetProgramType(prog), mp.ProgramType.kQP)

    def test_mathematical_program_result(self):
        result = MathematicalProgramResult()
        self.assertEqual(result.get_solution_result(),
                         mp.SolutionResult.kSolutionResultNotSet)

    def test_solve_in_parallel(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2)
        prog.AddLinearConstraint(x[0] + x[1] == 2)
        prog.AddQuadraticCost(x[0] ** 2, is_convex=True)

        num_progs = 4
        progs = [prog for _ in range(num_progs)]
        initial_guesses = [np.zeros(2) for _ in range(num_progs)]
        solver_ids = [ScsSolver().solver_id() for _ in range(num_progs)]
        solver_options = [SolverOptions() for _ in range(num_progs)]

        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=initial_guesses,
                                     solver_options=solver_options,
                                     solver_ids=solver_ids,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=None,
                                     solver_options=solver_options,
                                     solver_ids=solver_ids,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=initial_guesses,
                                     solver_options=None,
                                     solver_ids=solver_ids,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=initial_guesses,
                                     solver_options=solver_options,
                                     solver_ids=solver_ids,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        # Finally, interleave None into initial_guesses, solver_options, and
        # solver_ids.
        initial_guesses[0] = None
        solver_options[0] = None
        solver_ids[0] = None
        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=initial_guesses,
                                     solver_options=solver_options,
                                     solver_ids=solver_ids,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        # Now we test the overload
        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=None,
                                     solver_options=SolverOptions(),
                                     solver_id=ScsSolver().solver_id(),
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=initial_guesses,
                                     solver_options=SolverOptions(),
                                     solver_id=ScsSolver().solver_id(),
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        # Ensure that all options being None does not cause ambiguity.
        results = mp.SolveInParallel(progs=progs,
                                     initial_guesses=None,
                                     solver_options=None,
                                     solver_id=None,
                                     parallelism=Parallelism.Max(),
                                     dynamic_schedule=False)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

        # Ensure default arguments do not cause ambiguity.
        results = mp.SolveInParallel(progs=progs)
        self.assertEqual(len(results), len(progs))
        self.assertTrue(all([r.is_success() for r in results]))

    def test_cost_binding(self):
        prog = mp.MathematicalProgram()
        x = prog.NewContinuousVariables(2)
        bound_cost = prog.AddCost(x[0] + x[1])
        # Ensure that bound_cost is of type mp.Binding[mp.Cost].
        self.assertTrue(isinstance(bound_cost, mp.Binding[mp.Cost]))
        self.assertFalse(isinstance(bound_cost, mp.Binding[mp.LinearCost]))
        prog.AddCost(binding=bound_cost)


class DummySolverInterface(SolverInterface):

    ID = SolverId("dummy")

    def __init__(self):
        SolverInterface.__init__(self)
        self.can_solve = False

    def available(self):
        return True

    def enabled(self):
        return True

    def solver_id(self):
        return DummySolverInterface.ID

    def Solve(self, prog, initial_guess=None, solver_options=None,
              result=None):
        # TODO(jwnimmer-tri) This trampoline for Solve is quite awkward.
        if result is not None:
            self._DoSolve(prog, initial_guess, solver_options, result)
            return
        else:
            result = mp.MathematicalProgramResult()
            self._DoSolve(prog, initial_guess, solver_options, result)
            return result

    def _DoSolve(self, prog, initial_guess, solver_options, result):
        assert isinstance(result, mp.MathematicalProgramResult)
        if not self.can_solve:
            raise Exception("Dummy solver cannot solve")
        # TODO(jwnimmer-tri) We should be setting the result here, but the
        # result class doesn't have any setters bound!  I'm not sure why we
        # have a Solve trampoline in the first place, if no solver can ever
        # produce any results?

    def AreProgramAttributesSatisfied(self, prog):
        return self.can_solve


class TestSolverInterface(unittest.TestCase):
    def test_dummy_solver_interface(self):
        solver = DummySolverInterface()
        self.assertTrue(solver.available())
        self.assertTrue(solver.enabled())
        self.assertEqual(solver.solver_id().name(), "dummy")
        self.assertIsInstance(solver, SolverInterface)
        prog = mp.MathematicalProgram()
        with self.assertRaisesRegex(Exception, "Dummy.*cannot solve"):
            unused_result = mp.MathematicalProgramResult()
            solver.Solve(prog, None, None, unused_result)
        with self.assertRaisesRegex(Exception, "Dummy.*cannot solve"):
            _ = solver.Solve(prog)
        self.assertFalse(solver.AreProgramAttributesSatisfied(prog))
        solver.can_solve = True
        self.assertTrue(solver.AreProgramAttributesSatisfied(prog))
        result = solver.Solve(prog)
        self.assertIsInstance(result, mp.MathematicalProgramResult)
