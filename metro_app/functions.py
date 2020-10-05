#!/usr/bin/env python
"""This file defines functions used by the other files.
Author: Lucas Javaudin
E-mail: lucas.javaudin@ens-paris-saclay.fr
"""

import subprocess
import csv
from io import StringIO
import numpy as np
import pandas as pd

from django.conf import settings
from django.db.models import Sum
from django.db import connection

from .models import *


def get_query(object_name, simulation):
    """Function used to return all instances of an object related to a
    simulation.
    """
    query = None
    if object_name == 'centroid':
        query = Centroid.objects.filter(
            network__supply__scenario__simulation=simulation
        )
    elif object_name == 'crossing':
        query = Crossing.objects.filter(
            network__supply__scenario__simulation=simulation
        )
    elif object_name == 'link':
        query = Link.objects.filter(
            network__supply__scenario__simulation=simulation
        )
    elif object_name == 'function':
        query = Function.objects.filter(
            functionset__supply__scenario__simulation=simulation
        )
    elif object_name == 'usertype':
        query = UserType.objects.filter(
            demandsegment__demand__scenario__simulation=simulation
        )
    elif object_name == 'demandsegment':
        query = DemandSegment.objects.filter(
            demand__scenario__simulation=simulation
        )
    elif object_name == 'matrices':
        query = Matrices.objects.filter(
            demandsegment__demand__scenario__simulation=simulation
        )
    elif object_name == 'run':
        query = SimulationRun.objects.filter(
            simulation=simulation
        )
    elif object_name == 'public_transit':
        if simulation.scenario.supply.pttimes:  # Variable pttimes can be null.
            query = Matrix.objects.filter(
                matrices=simulation.scenario.supply.pttimes
            )
    elif object_name == 'policy':
        query = Policy.objects.filter(scenario=simulation.scenario)
    return query


def can_view(user, simulation):
    """Check if the user can view a specific simulation.
    The user can view the simulation if the simulation is public, if he owns
    the simulation or if he is a superuser.
    """
    env_users = None
    if simulation.environment is not None:
        env_users = simulation.environment.users.all().filter(id=user.id)

    if simulation.public or simulation.user == user or user.is_superuser or \
            env_users:
        return True
    else:
        return False


def can_edit(user, simulation):
    """Check if the user can edit a specific simulation.
    The user can edit the simulation if he owns the simulation or if he is a
    superuser.
    """
    if simulation.user == user or user.is_superuser:
        return True
    else:
        return False


def can_edit_environment(user, environment):
    """Check if the user can edit a specific environment.
    """
    if environment.creator == user or user.is_superuser:
        return True
    else:
        return False


def metro_to_user(object):
    """Convert the name of a network object (used in the source code) to a name
    suitable for users.
    """
    if object == 'centroid':
        return 'zone'
    elif object == 'crossing':
        return 'intersection'
    elif object == 'link':
        return 'link'
    elif object == 'function':
        return 'congestion function'
    return ''


def custom_check_test(value):
    """Custom check_test to convert metropolis string booleans into Python
    booleans.
    """
    if value == 'true':
        return True
    else:
        return False


def get_node_choices(simulation):
    """Return all the nodes (centroids and crossings) related to a simulation.
    These nodes can be origin or destination of links.
    """
    centroids = Centroid.objects.filter(
        network__supply__scenario__simulation=simulation
    )
    crossings = Crossing.objects.filter(
        network__supply__scenario__simulation=simulation
    )
    centroid_choices = [(centroid.id, str(centroid)) for centroid in centroids]
    crossing_choices = [(crossing.id, str(crossing)) for crossing in crossings]
    node_choices = centroid_choices + crossing_choices
    return node_choices


def run_simulation(run):
    """Function to start a SimulationRun.
    This function writes the argument file of Metropolis, then runs two scripts
    and Metrosim.
    """
    # Write the argument file used by metrosim.
    simulation = run.simulation
    metrosim_dir = settings.BASE_DIR + '/metrosim_files/'
    metrosim_file = '{0}execs/metrosim'.format(metrosim_dir)
    arg_file = (
        '{0}arg_files/simulation_{1!s}_run_{2!s}.txt'.format(metrosim_dir,
                                                             simulation.id,
                                                             run.id)
    )
    with open(arg_file, 'w') as f:
        database = settings.DATABASES['default']
        db_host = database['HOST']
        db_name = database['NAME']
        db_user = database['USER']
        db_pass = database['PASSWORD']
        log = metrosim_dir + 'logs/run_{}.txt'.format(run.id)
        tmp = metrosim_dir + 'output'
        stop = metrosim_dir + 'stop_files/run_{}.stop'.format(run.id)
        if simulation.random_seed_check:
            random_seed = simulation.random_seed
        else:
            random_seed = -1
        arguments = ('-dbHost "{0}" -dbName "{1}" -dbUser "{2}" '
                     + '-dbPass "{3}" -logFile "{4}" -tmpDir "{5}" '
                     + '-stopFile "{6}" -simId "{7!s}" -runId "{8!s}" '
                     + '-randomSeed "{9!s}"'
                     ).format(db_host, db_name, db_user, db_pass, log, tmp,
                              stop, simulation.id, run.id, random_seed)
        f.write(arguments)

    # Run the script 'prepare_run.py' then run metrosim then run the script
    # 'run_end.py'.
    # The two scripts are run with the run.id as an argument.
    prepare_run_file = settings.BASE_DIR + '/metro_app/prepare_run.py'
    build_results_file = settings.BASE_DIR + '/metro_app/build_results.py'
    log_file = (
        '{0}/website_files/script_logs/run_{1}.txt'.format(
            settings.BASE_DIR, run.id
        )
    )
    # Command looks like:
    #
    # python3 ./metro_app/prepare_results.py y
    # 2>&1 | tee ./website_files/script_logs/run_y.txt
    # && ./metrosim_files/execs/metrosim
    # ./metrosim_files/arg_files/simulation_x_run_y.txt
    # && python3 ./metro_app/build_results.py y
    # 2>&1 | tee ./website_files/script_logs/run_y.txt
    #
    # 2>&1 | tee is used to redirect output and errors to file.
    command = ('python3 {first_script} {run_id} 2>&1 | tee {log} && '
               + '{metrosim} {argfile} && '
               + 'python3 {second_script} {run_id} 2>&1 | tee {log}')
    command = command.format(first_script=prepare_run_file, run_id=run.id,
                             log=log_file, metrosim=metrosim_file,
                             argfile=arg_file,
                             second_script=build_results_file)
    subprocess.Popen(command, shell=True)


def object_import_function(encoded_file, simulation, object_name):
    """Function to import a file representing the input of a simulation.
    Parameters
    ----------
    encoded_file: File object.
        Input file, as given by request.FILES.
    simulation: Simulation object.
        Simulation to modify.
    object_name: String.
        Name of the input to modify: 'centroid', 'crossing', 'link' or
        'function'.
    This function could be much more simple but I tried to use as little as
    possible Django ORM (the querysets) to speed up the view.
    Basically, this view looks at each row in the imported file to find if an
    instance already exists for the row (user_id already used) or if a new
    instance needs to be created. If the instance already exists, the view
    compares the values in the file with the values in the database to know if
    the instance needs to be updated.
    Python built-in set are used to perform comparison of arrays quickly.
    """
    if object_name == 'function':
        parent = simulation.scenario.supply.functionset
    else:
        parent = simulation.scenario.supply.network
    query = get_query(object_name, simulation)
    user_id_set = set(query.values_list('user_id', flat=True))
    if object_name == 'link':
        # To import links, we retrieve the ids and user ids of all
        # centroids, crossings and functions in DataFrames.
        cols = ['user_id', 'id']
        centroids = get_query('centroid', simulation)
        centroid_df = pd.DataFrame(list(centroids.values_list(*cols)),
                                   columns=cols)
        crossings = get_query('crossing', simulation)
        crossing_df = pd.DataFrame(list(crossings.values_list(*cols)),
                                   columns=cols)
        node_df = pd.concat([centroid_df, crossing_df])
        functions = get_query('function', simulation)
        function_df = pd.DataFrame({
            'user_id': list(functions.values_list('user_id', flat=True)),
            'id': list(functions.values_list('id', flat=True)),
            'instance': list(functions),
        })
    # Convert imported file to a csv DictReader.
    tsv_file = StringIO(encoded_file.read().decode())
    if encoded_file.name.split(".")[-1] == 'tsv':
        df = pd.read_csv(tsv_file, delimiter='\t')
    else:
        df = pd.read_csv(tsv_file, delimiter=',')
    if 'name' in df.columns:
        df['name'] = df['name'].astype(str)
    else:
        # Name column is optionnal.
        # Create the column if it does not exist.
        df['name'] = ''
    if object_name in ('centroid', 'crossing'):
        if object_name == 'centroid':
            # Do not import centroid with same id as a crossing.
            crossings = get_query('crossing', simulation)
            imported_ids = crossings.values_list('user_id', flat=True)
        else:
            # Do not import crossing with same id as a centroid.
            centroids = get_query('centroid', simulation)
            imported_ids = set(centroids.values_list('user_id', flat=True))
        df = df[['id', 'name', 'x', 'y']]
        df = df.loc[~df['id'].isin(imported_ids)]
    elif object_name == 'function':
        df = df[['id', 'name', 'expression']]
    elif object_name == 'link':
        # We need to add useful columns to the DataFrame.
        # Add column id_origin with the id of the origin node.
        df = df.merge(node_df, left_on='origin', right_on='user_id',
                      suffixes=('', '_origin'))
        # Add column id_destination with the id of the destination
        # node.
        df = df.merge(node_df, left_on='destination',
                      right_on='user_id',
                      suffixes=('', '_destination'))
        # Add column instance_function with the function object of the
        # given user_id.
        df = df.merge(function_df, left_on='function',
                      right_on='user_id', suffixes=('', '_function'))
        df = df[['id', 'name', 'id_origin', 'id_destination',
                 'id_function', 'instance', 'length', 'lanes', 'speed',
                 'capacity']]
    # Remove duplicated ids.
    df = df.drop_duplicates(subset='id', keep='last')
    # Check if user_id already exists.
    df['new'] = ~(df['id'].isin(user_id_set))
    if not df['new'].all():
        # Some existing objects needs to be updated.
        if object_name == 'link':
            new_values = set(
                map(tuple, df.loc[~df['new']].drop(
                    columns=['new', 'instance']
                ).values)
            )
        else:
            new_values = set(
                map(tuple, df.loc[~df['new']].drop(columns='new').values)
            )
        if object_name in ('centroid', 'crossing'):
            old_values = set(
                query.values_list('user_id', 'name', 'x', 'y')
            )
        elif object_name == 'function':
            old_values = set(
                query.values_list('user_id', 'name', 'expression')
            )
        elif object_name == 'link':
            old_values = set(
                query.values_list('user_id', 'name', 'origin',
                                  'destination', 'vdf_id', 'length',
                                  'lanes', 'speed', 'capacity')
            )
        # Find the instances that really need to be updated (i.e. the
        # values have changed).
        new_values = new_values.difference(old_values)
        if object_name in ('centroid', 'crossing', 'function'):
            # Update the objects (it would be faster to delete and
            # re-create them but this would require to also change the
            # foreign keys of the links).
            for values in new_values:
                # Index 0 of values is the id column i.e. the user_id.
                instance = query.filter(user_id=values[0])
                if object_name in ('centroid', 'crossing'):
                    instance.update(
                        name=values[1], x=values[2], y=values[3]
                    )
                else:  # Function
                    instance.update(name=values[1], expression=values[2])
        elif object_name == 'link':
            # Delete the updated links.
            user_ids = [values[0] for values in new_values]
            old_links = query.filter(user_id__in=user_ids)
            old_links.delete()
            # Re-create the updated links with the new links.
            df.loc[df['id'].isin(user_ids), 'new'] = True
    if df['new'].any():
        # Only keep the new objects.
        df = df.loc[df['new']]
        # Create the new objects in bulk.
        # The chunk size is limited by the MySQL engine (timeout if it is
        # too big).
        chunk_size = 10000
        new_objects = list()
        if object_name == 'centroid':
            for key, row in df.iterrows():
                new_objects.append(
                    Centroid(user_id=row['id'], name=row['name'],
                             x=row['x'], y=row['y'])
                )
        elif object_name == 'crossing':
            for key, row in df.iterrows():
                new_objects.append(
                    Crossing(user_id=row['id'], name=row['name'],
                             x=row['x'], y=row['y'])
                )
        elif object_name == 'function':
            for key, row in df.iterrows():
                new_objects.append(
                    Function(user_id=row['id'], name=row['name'],
                             expression=row['expression'])
                )
        elif object_name == 'link':
            for key, row in df.iterrows():
                new_objects.append(
                    Link(user_id=row['id'], name=row['name'],
                         origin=row['id_origin'],
                         destination=row['id_destination'],
                         vdf=row['instance'], length=row['length'],
                         lanes=row['lanes'], speed=row['speed'],
                         capacity=row['capacity'])
                )
        chunks = [new_objects[x:x + chunk_size]
                  for x in range(0, len(new_objects), chunk_size)]
        for chunk in chunks:
            # Create the new instances.
            query.model.objects.bulk_create(chunk, chunk_size)
        # Retrieve new ids.
        last_id = query.model.objects.last().id
        new_ids = np.arange(last_id - len(new_objects) + 1, last_id + 1)
        # Add the many-to-many relation to Network or FunctionSet.
        relations = list()
        if object_name == 'function':
            for new_id in new_ids:
                relations.append(
                    query.model.functionset.through(
                        functionset_id=parent.id, function_id=new_id
                    )
                )
            query.model.functionset.through.objects.bulk_create(
                relations, batch_size=chunk_size
            )
        else:
            # Pass arguments as dict to avoid more if conditions.
            object_id = '{}_id'.format(object_name)
            for new_id in new_ids:
                relations.append(
                    query.model.network.through(
                        **{'network_id': parent.id, object_id: new_id}
                    )
                )
            query.model.network.through.objects.bulk_create(
                relations, batch_size=chunk_size
            )
    simulation.has_changed = True
    simulation.save()


def matrix_import_function(encoded_file, simulation, demandsegment):
    """Function to import a file representing the OD matrix of a usertype.

    Parameters
    ----------
    encoded_file: File object.
        Input file, as given by request.FILES.
    simulation: Simulation object.
        Simulation to modify.
    demandsegment: DemandSegment object.
        Demand segment for which the OD matrix must be modified.
    This function could be much more simple but I tried to use as little as
    possible Django ORM (the querysets). When written with standard Django
    querysets and save methods, it took hours to import a large OD matrix.
    Basicaly, this view looks at each row in the imported file to know if the
    OD pair of the row already exists or needs to be created. If it already
    exists, the view looks at the population value to know if the value needs
    to be updated. To update values, we simply delete the previous entries in
    the database and insert the new ones.
    """
    # Create a set with all existing OD pairs in the OD matrix.
    matrix = demandsegment.matrix
    pairs = Matrix.objects.filter(matrices=matrix)
    existing_pairs = set(pairs.values_list('p_id', 'q_id'))
    # Create a dictionary to map the centroid user ids with the centroid
    # objects.
    centroids = get_query('centroid', simulation)
    centroid_mapping = dict()
    centroid_id_mapping = dict()
    for centroid in centroids:
        centroid_mapping[centroid.user_id] = centroid
        centroid_id_mapping[centroid.user_id] = centroid.id
    # Convert the imported file to a csv DictReader.
    tsv_file = StringIO(encoded_file.read().decode())
    if encoded_file.name.split(".")[-1] == 'tsv':
        reader = csv.DictReader(tsv_file, delimiter='\t')
    else:
        reader = csv.DictReader(tsv_file, delimiter=',')
    # For each imported OD pair, if the pair already exists in t
    # For each imported OD pair, if the pair already exists in the OD Matrix,
    # it is stored to be updated, else it is stored to be created.
    to_be_updated = set()
    to_be_created = list()
    for row in reader:
        pair = (
            centroid_id_mapping[int(row['origin'])],
            centroid_id_mapping[int(row['destination'])]
        )
        if pair in existing_pairs:
            to_be_updated.add((*pair, float(row['population'])))
        elif float(row['population']) > 0:
            to_be_created.append(
                Matrix(p=centroid_mapping[int(row['origin'])],
                       q=centroid_mapping[int(row['destination'])],
                       r=float(row['population']),
                       matrices=matrix)
            )
    if to_be_updated:
        # Create a mapping between the values (p, q, r) and the ids.
        pair_values = set(pairs.values_list('id', 'p_id', 'q_id'))
        pair_mapping = dict()
        for pair in pair_values:
            pair_mapping[pair[1:]] = pair[0]
        pair_values = set(pairs.values_list('id', 'p_id', 'q_id', 'r'))
        # Find the pairs that really need to be updated (i.e. r is also
        # different).
        pair_values = set(pairs.values_list('p_id', 'q_id', 'r'))
        to_be_updated = to_be_updated.difference(pair_values)
        # Retrieve the ids of the pairs to be updated with the mapping and
        # delete them.
        to_be_updated_ids = [pair_mapping[pair[:2]] for pair in to_be_updated]
        with connection.cursor() as cursor:
            chunk_size = 20000
            chunks = [to_be_updated_ids[x:x + chunk_size]
                      for x in range(0, len(to_be_updated_ids), chunk_size)]
            for chunk in chunks:
                cursor.execute(
                    "DELETE FROM Matrix "
                    "WHERE id IN %s;",
                    [chunk]
                )
        # Create a mapping between the centroids ids and the centroid objects.
        centroid_id_mapping = dict()
        for centroid in centroids:
            centroid_id_mapping[centroid.id] = centroid
        # Now, create the updated pairs with the new values.
        to_be_created += [
            Matrix(p=centroid_id_mapping[pair[0]],
                   q=centroid_id_mapping[pair[1]],
                   r=pair[2],
                   matrices=matrix)
            for pair in to_be_updated
        ]
    # Create the new OD pairs in bulk.
    # The chunk size is limited by the MySQL engine (timeout if it is too big).
    chunk_size = 20000
    chunks = [to_be_created[x:x + chunk_size]
              for x in range(0, len(to_be_created), chunk_size)]
    for chunk in chunks:
        Matrix.objects.bulk_create(chunk, chunk_size)
    # Update total.
    pairs = pairs.all()  # Update queryset from database.
    matrix.total = int(
        demandsegment.scale * pairs.aggregate(Sum('r'))['r__sum']
    )
    matrix.save()
    simulation.has_changed = True
    simulation.save()


def pricing_import_function(encoded_file, simulation):
    """Function to import a file as tolls in the database.
    Parameters
    ----------
    encoded_file: File object.
        Input file, as given by request.FILES.
    simulation: Simulation object.
        Simulation to modify.
    """
    # Get all pricing policies for this usertype.
    policies = get_query('policy', simulation)
    tolls = policies.filter(type='PRICING')
    # Get all links of the network.
    links = get_query('link', simulation)
    # Get all LinkSelection of the network.
    locations = LinkSelection.objects.filter(
        network=simulation.scenario.supply.network
    )
    # Get all usertypes.
    usertypes = get_query('usertype', simulation)
    # Get an empty Vector or create one if there is none.
    if Vector.objects.filter(data='').exists():
        empty_vector = Vector.objects.filter(data='')[0]
    else:
        empty_vector = Vector(data='')
        empty_vector.save()
    # Convert the imported file to a csv DictReader.
    tsv_file = StringIO(encoded_file.read().decode())
    if encoded_file.name.split(".")[-1] == 'tsv':
        reader = csv.DictReader(tsv_file, delimiter='\t')
    else:
        reader = csv.DictReader(tsv_file, delimiter=',')
    # For each imported OD pair, if the pair already exists in t
    if 'traveler_type' in reader.fieldnames:
        has_type = True
    else:
        has_type = False
    if 'times' in reader.fieldnames:
        has_times = True
    else:
        has_times = False
    # For each imported link, if a Policy exists for the link, baseValue is
    # updated, else a new Policy is created.
    for row in reader:
        # Get link of current row.
        link = links.get(user_id=row['link'])
        # Get or create a LinkSelection associated with the link.
        if locations.filter(link=link).exists():
            # Take first matching LinkSelection.
            location = locations.filter(link=link)[0]
        else:
            # Create a LinkSelection for the current link.
            # Name and user_id of the Link Selection are set to the name
            # and user_id of the link.
            location = LinkSelection(
                network=simulation.scenario.supply.network,
                name=link.name,
                user_id=link.user_id,
            )
            location.save()
            location.link.add(link)
        # Get or create a pricing Policy with the corret LinkSelection
        # object.
        try:
            toll = tolls.get(location=location)
        except Policy.DoesNotExist:
            # Create a new toll with default values.
            toll = Policy(location=location, type='PRICING', usertype=None,
                          valueVector=empty_vector,
                          timeVector=empty_vector)
            toll.save()
            toll.scenario.add(simulation.scenario)
        # Update affected traveler type.
        toll.usertype = None
        if has_type:
            try:
                toll.usertype = usertypes.get(user_id=row['traveler_type'])
            except (UserType.DoesNotExist, ValueError):
                pass
        # Update values.
        values = row['values'].split(',')
        # First value is baseValue.
        toll.baseValue = float(values[0])
        if len(values) > 1:
            # Remaining values are stored in valueVector (as a string of
            # comma separated values).
            values = [str(float(x)) for x in values]
            v = Vector(data=','.join(values[1:]))
            v.save()
            toll.valueVector = v
        else:
            toll.valueVector = empty_vector
        # Update times.
        toll.timeVector = empty_vector
        if has_times:
            times = row['times'].split(',')
            if times[0] != ' ' and times[0]:
                # There is at least one value, store it in timeVector.
                times = [str(int(x)) for x in times]
                v = Vector(data=','.join(times))
                v.save()
                toll.timeVector = v
        toll.save()


def public_transit_import_function(encoded_file, simulation):
    """Function to import a file as a public transit matrix in the database.
    Parameters
    ----------
    encoded_file: File object.
        Input file, as given by request.FILES.
    simulation: Simulation object.
        Simulation to modify.
    """
    # Create a set with all existing OD pairs in the OD matrix.
    matrix = simulation.scenario.supply.pttimes
    pairs = get_query('public_transit', simulation)
    existing_pairs = set(pairs.values_list('p_id', 'q_id'))
    # Create a dictionary to map the centroid user ids with the centroid
    # objects.
    centroids = get_query('centroid', simulation)
    centroid_mapping = dict()
    centroid_id_mapping = dict()
    for centroid in centroids:
        centroid_mapping[centroid.user_id] = centroid
        centroid_id_mapping[centroid.user_id] = centroid.id
    # Convert the imported file to a csv DictReader.
    tsv_file = StringIO(encoded_file.read().decode())
    if encoded_file.name.split(".")[-1] == 'tsv':
        reader = csv.DictReader(tsv_file, delimiter='\t')
    else:
        reader = csv.DictReader(tsv_file, delimiter=',')
    # For each imported OD pair, if the pair already exists in the OD Matrix,
    # it is stored to be updated, else it is stored to be created.
    to_be_updated = set()
    to_be_created = list()
    for row in reader:
        pair = (
            centroid_id_mapping[int(row['origin'])],
            centroid_id_mapping[int(row['destination'])]
        )
        if pair in existing_pairs:
            to_be_updated.add((*pair, float(row['travel time'])))
        else:
            to_be_created.append(
                Matrix(p=centroid_mapping[int(row['origin'])],
                       q=centroid_mapping[int(row['destination'])],
                       r=float(row['travel time']),
                       matrices=matrix)
            )
    if to_be_updated:
        # Create a mapping between the values (p, q, r) and the ids.
        pair_values = set(pairs.values_list('id', 'p_id', 'q_id'))
        pair_mapping = dict()
        for pair in pair_values:
            pair_mapping[pair[1:]] = pair[0]
        # Find the pairs that really need to be updated (i.e. r is also
        # different).
        pair_values = set(pairs.values_list('p_id', 'q_id', 'r'))
        to_be_updated = to_be_updated.difference(pair_values)
        # Retrieve the ids of the pairs to be updated with the mapping and
        # delete them.
        to_be_updated_ids = [pair_mapping[pair[:2]] for pair in to_be_updated]
        with connection.cursor() as cursor:
            chunk_size = 20000
            chunks = [to_be_updated_ids[x:x + chunk_size]
                      for x in range(0, len(to_be_updated_ids), chunk_size)]
            for chunk in chunks:
                cursor.execute(
                    "DELETE FROM Matrix "
                    "WHERE id IN %s;",
                    [chunk]
                )
        # Create a mapping between the centroids ids and the centroid objects.
        centroid_id_mapping = dict()
        for centroid in centroids:
            centroid_id_mapping[centroid.id] = centroid
        # Now, create the updated pairs with the new values.
        to_be_created += [
            Matrix(p=centroid_id_mapping[pair[0]],
                   q=centroid_id_mapping[pair[1]],
                   r=pair[2],
                   matrices=matrix)
            for pair in to_be_updated
        ]
    # Create the new OD pairs in bulk.
    # The chunk size is limited by the MySQL engine (timeout if it is too big).
    chunk_size = 20000
    chunks = [to_be_created[x:x + chunk_size]
              for x in range(0, len(to_be_created), chunk_size)]
    for chunk in chunks:
        Matrix.objects.bulk_create(chunk, chunk_size)


def usertype_import_function(encoded_file, simulation):
    """Function to import a file as a new usertype in the database.
    Parameters
    ----------
    encoded_file: File object.
        Input file, as given by request.FILES.
    simulation: Simulation object.
        Simulation to modify.
    """
    # Get an empty Vector or create one if there is none.
    if Vector.objects.filter(data='').exists():
        empty_vector = Vector.objects.filter(data='')[0]
    else:
        empty_vector = Vector(data='')
        empty_vector.save()
    # Convert the imported file to a csv DictReader.
    tsv_file = StringIO(encoded_file.read().decode())
    if encoded_file.name.split(".")[-1] == 'zip':
        reader = csv.DictReader(tsv_file, delimiter='\t')
    else:
        reader = csv.DictReader(tsv_file, delimiter=',')

    for row in reader:

        name = row['name']
        comment = row['comment']

        alphaTI_mean = row['alphaTI_mean']
        alphaTI_std = row['alphaTI_std']
        alphaTI_type = row['alphaTI_type']
        alphaTI = Distribution(mean=alphaTI_mean,
                               std=alphaTI_std,
                               type=alphaTI_type)
        alphaTI.save()

        alphaTP_mean = row['alphaTP_mean']
        alphaTP_std = row['alphaTP_std']
        alphaTP_type = row['alphaTP_type']
        alphaTP = Distribution(mean=alphaTP_mean, std=alphaTP_std, type=alphaTP_type)
        alphaTP.save()

        beta_mean = row['beta_mean']
        beta_std = row['beta_std']
        beta_type = row['beta_type']
        beta = Distribution(mean=beta_mean,
                            std=beta_std,
                            type=beta_type)
        beta.save()

        delta_mean = row['delta_mean']
        delta_std = row['delta_std']
        delta_type = row['delta_type']
        delta = Distribution(mean=delta_mean,
                             std=delta_std,
                             type=delta_type)
        delta.save()

        departureMu_mean = row['departureMu_mean']
        departureMu_std = row['departureMu_std']
        departureMu_type = row['departureMu_type']
        departureMu = Distribution(mean=departureMu_mean,
                                   std=departureMu_std,
                                   type=departureMu_type)
        departureMu.save()

        gamma_mean = row['gamma_mean']
        gamma_std = row['gamma_std']
        gamma_type = row['gamma_type']
        gamma = Distribution(mean=gamma_mean,
                             std=gamma_std,
                             type=gamma_type)
        gamma.save()

        modeMu_mean = row['modeMu_mean']
        modeMu_std = row['modeMu_std']
        modeMu_type = row['modeMu_type']
        modeMu = Distribution(mean=modeMu_mean,
                              std=modeMu_std,
                              type=modeMu_type)
        modeMu.save()

        penaltyTP_mean = row['penaltyTP_mean']
        penaltyTP_std = row['penaltyTP_std']
        penaltyTP_type = row['penaltyTP_type']
        penaltyTP = Distribution(mean=penaltyTP_mean,
                                 std=penaltyTP_std,
                                 type=penaltyTP_type)
        penaltyTP.save()

        routeMu_mean = row['routeMu_mean']
        routeMu_std = row['routeMu_std']
        routeMu_type = row['routeMu_type']
        routeMu = Distribution(mean=routeMu_mean,
                               std=routeMu_std,
                               type=routeMu_type)
        routeMu.save()

        tstar_mean = row['tstar_mean']
        tstar_std = row['tstar_std']
        tstar_type = row['tstar_type']
        tstar = Distribution(mean=tstar_mean,
                             std=tstar_std,
                             type=tstar_type)
        tstar.save()

        typeOfRouteChoice = row['typeOfRouteChoice']
        typeOfDepartureMu = row['typeOfDepartureMu']
        typeOfRouteMu = row['typeOfRouteMu']
        typeOfModeMu = row['typeOfModeMu']
        localATIS = row['localATIS']
        modeChoice = row['modeChoice']
        modeShortRun = row['modeShortRun']
        commuteType = row['commuteType']

        usertypes = get_query('usertype', simulation)
        if usertypes.exists():
            user_id = usertypes.last().user_id + 1
        else:
            user_id = 1

        # usertype = UserType(name=name, comment=comment, alphaTI=alphaTI, alphaTP=alphaTP, beta=beta, delta=delta, departureMu=departureMu, gamma=gamma, modeMu=modeMu, penaltyTP=penaltyTP, routeMu=routeMu, tstar=tstar, typeOfRouteChoice=typeOfRouteChoice, localATIS=localATIS, modeChoice=modeChoice, modeShortRun=modeShortRun, commuteType=commuteType, user_id=user_id)
        usertype = UserType()
        usertype.alphaTI = alphaTI
        usertype.alphaTP = alphaTP
        usertype.beta = beta
        usertype.delta = delta
        usertype.departureMu = departureMu
        usertype.gamma = gamma
        usertype.modeMu = modeMu
        usertype.penaltyTP = penaltyTP
        usertype.routeMu = routeMu
        usertype.tstar = tstar
        usertype.user_id = user_id
        usertype.typeOfRouteChoice = typeOfRouteChoice
        usertype.typeOfDepartureMu = typeOfDepartureMu
        usertype.typeOfRouteMu = typeOfRouteMu
        usertype.typeOfModeMu = typeOfModeMu
        usertype.localATIS = localATIS
        usertype.modeChoice = modeChoice
        usertype.modeShortRun = modeShortRun
        usertype.commuteType = commuteType
        usertype.save()

        matrix = Matrices()
        matrix.save()
        demandsegment = DemandSegment()
        demandsegment.usertype = usertype
        demandsegment.matrix = matrix
        demandsegment.save()
        demandsegment.demand.add(simulation.scenario.demand)
