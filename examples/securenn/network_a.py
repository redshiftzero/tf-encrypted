from __future__ import absolute_import
import sys
import math
from typing import List

import tensorflow as tf
import tensorflow_encrypted as tfe

from examples.mnist.convert import decode


if len(sys.argv) >= 2:
    # config file was specified
    config_file = sys.argv[1]
    config = tfe.config.load(config_file)
else:
    # default to using local config
    config = tfe.LocalConfig([
        'server0',
        'server1',
        'crypto-producer',
        'model-trainer',
        'prediction-client'
    ])


def variable_summaries(var):
    """Attach a lot of summaries to a Tensor (for TensorBoard visualization)."""
    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean', mean)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
            tf.summary.scalar('stddev', stddev)
        tf.summary.scalar('max', tf.reduce_max(var))
        tf.summary.scalar('min', tf.reduce_min(var))
        tf.summary.histogram('histogram', var)


class ModelTrainer(tfe.io.InputProvider):

    BATCH_SIZE = 32
    ITERATIONS = 60000 // BATCH_SIZE
    EPOCHS = 15
    IN_N = 28 * 28
    HIDDEN_N = 128
    OUT_N = 10

    def build_data_pipeline(self):

        def normalize(image, label):
            x = tf.cast(image, tf.float32) / 255.
            image = (x - 0.1307) / 0.3081  # image = (x - mean) / std
            return image, label

        dataset = tf.data.TFRecordDataset(["./data/train.tfrecord"])
        dataset = dataset.map(decode)
        dataset = dataset.map(normalize)
        dataset = dataset.repeat()
        dataset = dataset.batch(self.BATCH_SIZE)

        iterator = dataset.make_one_shot_iterator()
        return iterator

    def build_training_graph(self, training_data) -> List[tf.Tensor]:
        j = self.IN_N
        k = self.HIDDEN_N
        m = self.OUT_N
        r_in = math.sqrt(12 / (j + k))
        r_hid = math.sqrt(12 / (2 * k))
        r_out = math.sqrt(12 / (k + m))

        # model parameters and initial values
        with tf.name_scope('weights_0'):
            w0 = tf.Variable(tf.random_uniform([j, k], minval=-r_in, maxval=r_in))
            variable_summaries(w0)
        with tf.name_scope('biases_0'):
            b0 = tf.Variable(tf.zeros([k]))
            variable_summaries(b0)
        with tf.name_scope('weights_1'):
            w1 = tf.Variable(tf.random_uniform([k, k], minval=-r_hid, maxval=r_hid))
            variable_summaries(w1)
        with tf.name_scope('biases_1'):
            b1 = tf.Variable(tf.zeros([k]))
            variable_summaries(b1)
        with tf.name_scope('weights_2'):
            w2 = tf.Variable(tf.random_uniform([k, m], minval=-r_out, maxval=r_out))
            variable_summaries(w2)
        with tf.name_scope('biases_1'):
            b2 = tf.Variable(tf.zeros([m]))
            variable_summaries(b2)
        params = [w0, b0, w1, b1, w2, b2]

        # optimizer and data pipeline
        optimizer = tf.train.AdamOptimizer(learning_rate=0.01)

        # training loop
        def loop_body(i):

            # get next batch
            x, y = training_data.get_next()

            # model construction
            layer0 = x
            layer1 = tf.nn.relu(tf.matmul(layer0, w0) + b0)
            layer2 = tf.nn.relu(tf.matmul(layer1, w1) + b1)
            layer3 = tf.matmul(layer2, w2) + b2
            predictions = layer3

            # # model construction
            # layer0 = x
            # with tf.name_scope('W0x_plus_b0_bf_relu'):
            #     layer1_bf_relu = tf.matmul(layer0, w0) + b0
            #     tf.summary.histogram('layer1_bf_relu', layer1_bf_relu)
            #
            # with tf.name_scope('W0x_plus_b0_aft_relu'):
            #     layer1_aft_relu = tf.nn.relu(layer1_bf_relu)
            #     tf.summary.histogram('layer1_aft_relu', layer1_aft_relu)
            #
            # with tf.name_scope('W1x_plus_b1_bf_relu'):
            #     layer2_bf_relu = tf.matmul(layer1_aft_relu, w1) + b1
            #     tf.summary.histogram('layer2_bf_relu', layer2_bf_relu)
            #
            # with tf.name_scope('W1x_plus_b1_aft_relu'):
            #     layer2_aft_relu = tf.nn.relu(layer2_bf_relu)
            #     tf.summary.histogram('layer2_aft_relu', layer2_aft_relu)
            #
            # with tf.name_scope('W2x_plus_b2'):
            #     layer3 = tf.matmul(layer2_aft_relu, w2) + b2
            #     tf.summary.histogram('layer3', layer3)
            #
            # predictions = layer3

            loss = tf.reduce_mean(tf.losses.sparse_softmax_cross_entropy(logits=predictions, labels=y))
            with tf.control_dependencies([optimizer.minimize(loss)]):
                return i + 1

        loop = tf.while_loop(lambda i: i < self.ITERATIONS * self.EPOCHS, loop_body, (0,))

        # return model parameters after training
        loop = tf.Print(loop, [], message="Training complete")
        with tf.control_dependencies([loop]):
            return [param.read_value() for param in params]

    def provide_input(self) -> List[tf.Tensor]:
        with tf.name_scope('loading'):
            training_data = self.build_data_pipeline()

        with tf.name_scope('training'):
            parameters = self.build_training_graph(training_data)

        return parameters


class PredictionClient(tfe.io.InputProvider, tfe.io.OutputReceiver):

    BATCH_SIZE = 20

    def build_data_pipeline(self):

        def normalize(image, label):
            x = tf.cast(image, tf.float32) / 255.
            image = (x - 0.1307) / 0.3081  # image = (x - mean) / std
            return image, label

        dataset = tf.data.TFRecordDataset(["./data/test.tfrecord"])
        dataset = dataset.map(decode)
        dataset = dataset.map(normalize)
        dataset = dataset.batch(self.BATCH_SIZE)

        iterator = dataset.make_one_shot_iterator()
        return iterator

    def provide_input(self) -> List[tf.Tensor]:
        with tf.name_scope('loading'):
            prediction_input, expected_result = self.build_data_pipeline().get_next()
            prediction_input = tf.Print(prediction_input, [expected_result], summarize=self.BATCH_SIZE, message="EXPECT ")

        with tf.name_scope('pre-processing'):
            prediction_input = tf.reshape(prediction_input, shape=(self.BATCH_SIZE, 28 * 28))

        return [prediction_input]

    def receive_output(self, tensors: List[tf.Tensor]) -> tf.Operation:
        likelihoods, = tensors
        with tf.name_scope('post-processing'):
            prediction = tf.argmax(likelihoods, axis=1)
            op = tf.Print([], [prediction], summarize=self.BATCH_SIZE, message="ACTUAL ")
            return op


model_trainer = ModelTrainer(config.get_player('model-trainer'))
prediction_client = PredictionClient(config.get_player('prediction-client'))

server0 = config.get_player('server0')
server1 = config.get_player('server1')
crypto_producer = config.get_player('crypto-producer')

with tfe.protocol.Pond(server0, server1, crypto_producer) as prot:

    # get model parameters as private tensors from model owner
    params = prot.define_private_input(model_trainer, masked=True)  # pylint: disable=E0632

    # we'll use the same parameters for each prediction so we cache them to avoid re-training each time
    params = prot.cache(params)

    # get prediction input from client
    x, = prot.define_private_input(prediction_client, masked=True)  # pylint: disable=E0632

    # compute prediction
    w0, b0, w1, b1, w2, b2 = params
    layer0 = x
    layer1 = prot.relu((prot.dot(layer0, w0) + b0))
    layer2 = prot.relu((prot.dot(layer1, w1) + b1))
    layer3 = prot.dot(layer2, w2) + b2
    prediction = layer3

    # send prediction output back to client
    prediction_op = prot.define_output([prediction], prediction_client)


with config.session() as sess:
    print("Init")
    tfe.run(sess, tf.global_variables_initializer(), tag='init')

    print("Training")
    tfe.run(sess, tfe.global_caches_updator(), tag='training')

    for _ in range(5):
        print("Predicting")
        tfe.run(sess, prediction_op, tag='prediction')