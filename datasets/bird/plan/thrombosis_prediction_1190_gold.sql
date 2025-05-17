{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "7",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 12,
                    "name": "$12",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 2,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 5,
                    "name": "$5",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "S",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 3,
                    "name": "$3",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "SCALAR_QUERY",
                    "operator": "$SCALAR_QUERY",
                    "operands": [],
                    "query": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "TIMES",
                            "operator": "*",
                            "type": "DOUBLE",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 0,
                                "name": "$0",
                                "type": "FLOAT"
                              },
                              {
                                "kind": "LITERAL",
                                "value": 1.2,
                                "type": "DECIMAL",
                                "nullable": false,
                                "precision": 2
                              }
                            ]
                          }
                        ],
                        "id": "4",
                        "inputs": [
                          {
                            "relOp": "LogicalAggregate",
                            "keys": [],
                            "aggs": [
                              {
                                "operator": "AVG",
                                "distinct": false,
                                "ignoreNulls": false,
                                "operands": [
                                  {
                                    "column": 0,
                                    "type": "FLOAT"
                                  }
                                ],
                                "type": "FLOAT",
                                "name": null
                              }
                            ],
                            "id": "3",
                            "inputs": [
                              {
                                "relOp": "LogicalProject",
                                "project": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 3,
                                    "name": "$3",
                                    "type": "FLOAT"
                                  }
                                ],
                                "id": "2",
                                "inputs": [
                                  {
                                    "relOp": "LogicalFilter",
                                    "condition": {
                                      "kind": "AND",
                                      "operator": "AND",
                                      "type": "BOOLEAN",
                                      "operands": [
                                        {
                                          "kind": "EQUALS",
                                          "operator": "=",
                                          "type": "BOOLEAN",
                                          "operands": [
                                            {
                                              "kind": "INPUT_REF",
                                              "index": 12,
                                              "name": "$12",
                                              "type": "INTEGER"
                                            },
                                            {
                                              "kind": "LITERAL",
                                              "value": 2,
                                              "type": "INTEGER",
                                              "nullable": false,
                                              "precision": 10
                                            }
                                          ]
                                        },
                                        {
                                          "kind": "EQUALS",
                                          "operator": "=",
                                          "type": "BOOLEAN",
                                          "operands": [
                                            {
                                              "kind": "INPUT_REF",
                                              "index": 5,
                                              "name": "$5",
                                              "type": "VARCHAR"
                                            },
                                            {
                                              "kind": "LITERAL",
                                              "value": "S",
                                              "type": "VARCHAR",
                                              "nullable": false,
                                              "precision": -1
                                            }
                                          ]
                                        }
                                      ]
                                    },
                                    "variableset": "[]",
                                    "id": "1",
                                    "inputs": [
                                      {
                                        "relOp": "LogicalTableScan",
                                        "table": "Examination",
                                        "id": "0",
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
            ]
          },
          "variableset": "[]",
          "id": "6",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Examination",
              "id": "5",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}