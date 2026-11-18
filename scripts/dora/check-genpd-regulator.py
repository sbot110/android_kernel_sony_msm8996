#!/usr/bin/env python3
"""Run real genpd-regulator callbacks against fault-injected runtime-PM calls.

The kernel callbacks are extracted without rewriting their control flow. This
checks regulator-vote lifetime and rollback, not physical GDSC sequencing.
"""
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / "drivers/regulator/qcom-genpd-regulator.c").read_text()


def function(name):
    start = source.index("static ", source.index(name) - 15)
    # Locate from declaration, not a later ops-table reference.
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
struct device { int users, get_result, put_result, gets, puts, noidle, noresume, disables; };
struct regulator_dev { void *data; };
struct qcom_genpd_regulator { struct device *dev; bool enabled; };
static void *rdev_get_drvdata(struct regulator_dev *rdev) { return rdev->data; }
static int pm_runtime_get_sync(struct device *d) { ++d->users; ++d->gets; return d->get_result; }
static int pm_runtime_put_sync(struct device *d) { --d->users; ++d->puts; return d->put_result; }
static void pm_runtime_put_noidle(struct device *d) { --d->users; ++d->noidle; }
static void pm_runtime_get_noresume(struct device *d) { ++d->users; ++d->noresume; }
static void pm_runtime_disable(struct device *d) { ++d->disables; }
'''
callbacks = "\n".join(function(name) for name in (
    "qcom_genpd_regulator_enable", "qcom_genpd_regulator_disable",
    "qcom_genpd_regulator_is_enabled", "qcom_genpd_regulator_cleanup"))
tests = r'''
int main(void)
{
    struct device d = {0};
    struct qcom_genpd_regulator p = { .dev = &d };
    struct regulator_dev r = { .data = &p };
    assert(qcom_genpd_regulator_is_enabled(&r) == 0);
    assert(qcom_genpd_regulator_disable(&r) == -EINVAL);
    assert(d.users == 0 && d.puts == 0);
    /* Failed resume increments PM usage internally; rollback must release it. */
    d.get_result = -EIO;
    assert(qcom_genpd_regulator_enable(&r) == -EIO);
    assert(!p.enabled && d.users == 0 && d.noidle == 1);
    d.get_result = -EPROBE_DEFER;
    assert(qcom_genpd_regulator_enable(&r) == -EPROBE_DEFER);
    assert(!p.enabled && d.users == 0 && d.noidle == 2);
    /* Both 0 and positive get_sync returns are successful. */
    d.get_result = 1;
    assert(qcom_genpd_regulator_enable(&r) == 0);
    assert(p.enabled && d.users == 1 && d.gets == 3);
    assert(qcom_genpd_regulator_enable(&r) == 0);
    assert(d.users == 1 && d.gets == 3);
    assert(qcom_genpd_regulator_is_enabled(&r) == 1);
    /* Failed suspend must retain exactly one logical vote for a later retry. */
    d.put_result = -EAGAIN;
    assert(qcom_genpd_regulator_disable(&r) == -EAGAIN);
    assert(p.enabled && d.users == 1 && d.noresume == 1);
    d.put_result = -EBUSY;
    assert(qcom_genpd_regulator_disable(&r) == -EBUSY);
    assert(p.enabled && d.users == 1 && d.noresume == 2);
    d.put_result = 0;
    assert(qcom_genpd_regulator_disable(&r) == 0);
    assert(!p.enabled && d.users == 0);
    assert(qcom_genpd_regulator_is_enabled(&r) == 0);
    assert(qcom_genpd_regulator_disable(&r) == -EINVAL);
    assert(d.puts == 3 && d.users == 0);
    d.get_result = 0;
    assert(qcom_genpd_regulator_enable(&r) == 0);
    qcom_genpd_regulator_cleanup(&p);
    assert(d.users == 0 && d.puts == 4 && d.disables == 1);
    /* Unused regulator removal must not release another consumer's vote. */
    d = (struct device){ .users = 2 };
    p = (struct qcom_genpd_regulator){ .dev = &d };
    qcom_genpd_regulator_cleanup(&p);
    assert(d.users == 2 && d.puts == 0 && d.disables == 1);
    puts("PASS: actual genpd callbacks: resume/suspend failures, retries, balanced votes and teardown");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="dora-genpd-") as directory:
    path = Path(directory)
    program = path / "test.c"
    program.write_text(shim + "\n#ifndef EPROBE_DEFER\n#define EPROBE_DEFER 517\n#endif\n" + callbacks + tests)
    subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) + ["-std=c99", "-Wall", "-Wextra", "-Werror", str(program), "-o", str(path / "test")], check=True)
    subprocess.run([str(path / "test")], check=True)
