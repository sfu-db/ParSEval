{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": true,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "VARCHAR"
        }
      ],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            },
            {
              "column": 1,
              "type": "VARCHAR"
            }
          ],
          "aggs": [],
          "id": "7",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "VARCHAR"
                }
              ],
              "id": "6",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 7,
                        "name": "$7",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "-",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalJoin",
                      "joinType": "inner",
                      "condition": {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 3,
                            "name": "$3",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 6,
                            "name": "$6",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      "id": "4",
                      "inputs": [
                        {
                          "relOp": "LogicalJoin",
                          "joinType": "inner",
                          "condition": {
                            "kind": "EQUALS",
                            "operator": "=",
                            "type": "BOOLEAN",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 1,
                                "name": "$1",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 3,
                                "name": "$3",
                                "type": "VARCHAR"
                              }
                            ]
                          },
                          "id": "2",
                          "inputs": [
                            {
                              "relOp": "LogicalTableScan",
                              "table": "atom",
                              "id": "0",
                              "inputs": []
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "molecule",
                              "id": "1",
                              "inputs": []
                            }
                          ]
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "bond",
                          "id": "3",
                          "inputs": []
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}