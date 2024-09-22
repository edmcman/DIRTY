"""
Usage:
    infer.py [options] CONFIG_FILE INPUT_JSON MODEL_CHECKPOINT

Options:
    -h --help                  Show this screen.
"""

from collections import defaultdict

from typing import Optional

from utils.ghidra_function import Function, CollectedFunction
from utils.ghidra_types import TypeLib, TypeInfo
from utils.ghidra_variable import Location, Stack, Register, Variable
from utils.dataset import Example, Dataset
from utils.code_processing import canonicalize_code

from model.model import TypeReconstructionModel

import _jsonnet

from docopt import docopt
import json

import torch

# Specialized version of dataset-gen-ghidra/decompiler/dump_trees.py

def ghidra_obtain_cf(ghidra_func):

    from ghidra.app.decompiler import DecompInterface

    # Specialized version from collect.py

    def collect_variables(variables):

        collected_vars = defaultdict(set)

        for v in variables:
            if v.getName() == "":
                continue

            typ: TypeInfo = TypeLib.parse_ghidra_type(v.getDataType())

            loc: Optional[Location] = None
            storage = v.getStorage()

            if storage.isStackStorage():
                loc = Stack(storage.getStackOffset())
            elif storage.isRegisterStorage():
                loc = Register(storage.getRegister().getName())
            else:
                print(f"Unknown storage type for {v} {v.getName()}: {storage}")
            if loc is not None:
                collected_vars[loc].add(Variable(typ=typ, name=v.getName(), user=False))
        return collected_vars

    decomp = DecompInterface()
    decomp.toggleSyntaxTree(False)
    decomp.openProgram(currentProgram())

    decomp_results = decomp.decompileFunction(ghidra_func, 30, None)

    if not decomp_results.decompileCompleted():
        raise RuntimeError("Failed to decompile")

    if decomp_results.getErrorMessage() != "":
        raise RuntimeError("Failed to decompile")

    high_func = decomp_results.getHighFunction()
    lsm = high_func.getLocalSymbolMap()
    symbols = [v for v in lsm.getSymbols()]
    func_return = high_func.getFunctionPrototype().getReturnType()

    name: str = ghidra_func.getName()

    return_type = TypeLib.parse_ghidra_type(func_return)

    arguments = collect_variables(
        [v for v in symbols if v.isParameter()],
    )
    local_vars = collect_variables(
        [v for v in symbols if not v.isParameter()],
    )

    raw_code = decomp_results.getCCodeMarkup().toString()

    decompiler = Function(
        ast=None,
        name=name,
        return_type=return_type,
        arguments=arguments,
        local_vars=local_vars,
        raw_code=raw_code,
    )

    cf = CollectedFunction(
        ea=ghidra_func.getEntryPoint().toString(),
        debug=None,
        decompiler=decompiler,
    )

    return cf

def infer(config, model, cf, binary_file=None):

    example = Example.from_cf(
        cf, binary_file=binary_file, max_stack_length=1024, max_type_size=1024
    )
    #print(example)

    assert example.is_valid_example, "Not a valid example, it probably has no variables"

    canonical_code = canonicalize_code(example.raw_code)
    example.canonical_code = canonical_code
    #print(example.canonical_code)

    # Create a dummy Dataset so we can call .annotate
    dataset = Dataset(config["data"]["test_file"], config["data"])

    #print(f"example src: {example.source}")
    #print(f"example target: {example.target}")

    example = dataset._annotate(example)

    collated_example = dataset.collate_fn([example])
    collated_example, _garbage = collated_example
    #print(collated_example)

    #tensor = torch.tensor([collated_example])
    #print(tensor)

    #single_example_loader = DataLoader([collated_example], batch_size=1)

    #trainer = pl.Trainer()
    #wat = trainer.predict(model, single_example_loader)
    #print(wat)

    with torch.no_grad():
        output = model(collated_example)

    var_names = [x[2:-2] for x in example.src_var_names]
    var_types = example.src_var_types_str

    pred_names = output['rename_preds']
    pred_types = output['retype_preds']

    output = {oldname: (newtype, newname) for (oldname, newname, newtype) in zip(var_names, pred_names, pred_types)}

    return output


def main(args):

    config = json.loads(_jsonnet.evaluate_file(args["CONFIG_FILE"]))

    json_dict = json.load(open(args["INPUT_JSON"], "r"))
    # print(json_dict)
    cf = CollectedFunction.from_json(json_dict)
    #print(cf)

    model = TypeReconstructionModel.load_from_checkpoint(checkpoint_path=args["MODEL_CHECKPOINT"], config=config) 
    model.eval()

    model_output = infer(config, model, cf, binary_file=args['INPUT_JSON'])

    print(f"The model output is: {model_output}")


if __name__ == "__main__":
    args = docopt(__doc__)
    main(args)
