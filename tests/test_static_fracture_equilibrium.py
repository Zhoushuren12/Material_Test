"""Force equilibrium must survive abrupt losses of parallel SMA branches."""
from pathlib import Path
import numpy as np
import pytest
from material_test.components2d.model import ComponentProject
from material_test.components2d.solver import run_component

SOLVER = Path(__file__).resolve().parents[1] / 'OS_terminal/OpenSees.exe'


@pytest.mark.skipif(not SOLVER.is_file(), reason='OpenSees unavailable')
def test_displacement_control_retains_force_balance_after_sma_fracture(tmp_path):
    resources = [{'tag': 102, 'name': 'steel', 'response': 'force_deformation',
                  'command': 'uniaxialMaterial Steel02 102 32130.757837223966 8521.692751127164 .0764996334630752 18 .925 .15 0 1 0 1'}]
    for branch in range(8):
        tag = 201 + branch
        limit = 64.85 + branch * .05
        resources.extend([
            {'tag': tag, 'name': f'SMA {branch}', 'response': 'force_deformation',
             'command': f'uniaxialMaterial SelfCentering {tag} 12782.486941857143 125.81755121689069 48195.364716175514 .580004375039825'},
            {'tag': tag+100, 'name': f'fracture {branch}', 'response': 'force_deformation',
             'command': f'uniaxialMaterial MinMax {tag+100} {tag} -min -{limit} -max {limit}'},
        ])
    tags = [102] * 12 + list(range(301, 309))
    elements = [{'tag': i+1, 'type': 'twoNodeLink', 'i': 1, 'j': 2,
                 'materials': [tag], 'directions': [1], 'axis': [1., 0.]} for i, tag in enumerate(tags)]
    project = ComponentProject({
        'schema': 'material-test/component-2d/v1', 'name': 'fracture-equilibrium',
        'solver': {'executable_path': str(SOLVER), 'timeout': 120},
        'units': {'force': 'N', 'length': 'mm', 'time': 's'}, 'output_dir': str(tmp_path),
        'nodes': [{'tag': 1, 'x': 0., 'y': 0., 'fix': [1, 1, 1]},
                  {'tag': 2, 'x': 1., 'y': 0., 'fix': [0, 1, 1]}],
        'materials': resources, 'elements': elements, 'constraints': [],
        'loading': {'analysis': 'static', 'node': 2, 'dof': 1,
                    'path': [0, 10, -10, 0, 60, -60, 0, 65.5, -65.5, 0, 70, -70, 0], 'step': .5},
        'channels': [{'name': 'load', 'quantity': 'load_factor'},
                     {'name': 'support', 'quantity': 'reaction', 'node': 1, 'dof': 1, 'factor': -1.},
                     {'name': 'sum', 'terms': [{'quantity': 'element', 'element': e['tag'], 'response': 'basicForce'} for e in elements]},
                     {'name': 'sma', 'terms': [{'quantity': 'element', 'element': e['tag'], 'response': 'basicForce'} for e in elements[12:]]}],
    }, tmp_path)
    result = run_component(project)
    np.testing.assert_allclose(result.channel('load'), result.channel('sum'), rtol=0., atol=2e-6)
    np.testing.assert_allclose(result.channel('support'), result.channel('sum'), rtol=0., atol=2e-6)
    input_values = result.channel('input')
    first_fracture = np.flatnonzero(input_values >= 65.5)[0]
    assert np.max(abs(result.channel('sma')[:first_fracture])) > 100000.
    np.testing.assert_allclose(result.channel('sma')[first_fracture:], 0., atol=1e-6)
